"""Home tab: Music player + synced LRCLIB lyrics + spectrum, Quick Tools grid, and Custom Launcher."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QFileInfo, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.config import ConfigManager
from ...core.i18n import tr
from ...core.storage import LauncherItem, LocalStorage
from ...services.lyrics_service import active_line_index
from ...services.media_service import MediaService, MediaStateSnapshot
from ..icons import get_icon, get_pixmap
from ..theme import ThemePalette, resolve_time_accent


def _fmt_time(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def _round_cover_pixmap(qimg: QImage, size: int, radius: int, dpr: float = 1.0) -> QPixmap:
    """Scale cover artwork with aspect-expanding center crop and antialiased rounded corners."""
    scale = max(1.0, float(dpr or 1.0))
    phys = max(1, int(round(size * scale)))
    phys_rad = float(radius) * scale
    src = QPixmap.fromImage(qimg)
    scaled = src.scaled(
        phys,
        phys,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    out = QPixmap(phys, phys)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(phys), float(phys), phys_rad, phys_rad)
    p.setClipPath(path)
    ox = (phys - scaled.width()) // 2
    oy = (phys - scaled.height()) // 2
    p.drawPixmap(ox, oy, scaled)
    p.end()
    out.setDevicePixelRatio(scale)
    return out


class SpectrumBarWidget(QWidget):
    """Real-time 20-band WASAPI loopback spectrum equalizer widget."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bands = [0.0] * 20
        self._accent = QColor("#3B82F6")
        self.setFixedHeight(26)

    def set_bands(self, bands: list[float]) -> None:
        if bands:
            self._bands = (bands + [0.0] * 20)[:20]
            self.update()

    def set_accent(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if c.isValid():
            self._accent = c

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = len(self._bands)
        gap = 3
        bw = max(2.0, (w - gap * (n - 1)) / n)
        p.setPen(Qt.PenStyle.NoPen)
        for i, val in enumerate(self._bands):
            v = max(0.06, min(1.0, float(val)))
            bh = v * (h - 2)
            x = i * (bw + gap)
            y = h - bh
            col = QColor(self._accent)
            col.setAlpha(int(120 + 135 * v))
            p.setBrush(col)
            p.drawRoundedRect(int(x), int(y), int(bw), int(bh), 2, 2)
        p.end()


def _favicon_cache_path(url: str, cache_dir: Path) -> Path:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path.split("/")[0]).lower()
    digest = hashlib.sha1(host.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return cache_dir / f"{digest}.png"


def fetch_website_favicon(url: str, dest: Path) -> bool:
    """Download and cache a site's genuine favicon as a clean 32x32 PNG."""
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc or parsed.path.split("/")[0]
    if not host:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    candidates = [
        f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(host)}&sz=64",
        f"https://{host}/favicon.ico",
        f"http://{host}/favicon.ico",
    ]
    for c_url in candidates:
        try:
            req = urllib.request.Request(c_url, headers={"User-Agent": "BorhIsland/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                raw = resp.read(262_144)
            img = QImage.fromData(raw)
            if not img.isNull() and img.width() >= 8:
                scaled = img.scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                if scaled.save(str(dest), "PNG"):
                    return True
        except Exception:
            continue
    return False


def _tight_crop_alpha(img: QImage) -> QImage:
    """Trim transparent borders, stray edge pixels, and soft Win11 shadows via row/col alpha density."""
    if img.isNull():
        return img
    argb = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = argb.width(), argb.height()

    def _scan_density(thresh: int, min_hits: int) -> tuple[int, int, int, int]:
        step = 2 if max(w, h) > 96 else 1
        mn_x, mx_x = w, -1
        for x in range(0, w, step):
            hits = 0
            for y in range(0, h, step):
                if argb.pixelColor(x, y).alpha() > thresh:
                    hits += 1
                    if hits >= min_hits:
                        if x < mn_x:
                            mn_x = x
                        if x > mx_x:
                            mx_x = x
                        break
        mn_y, mx_y = h, -1
        for y in range(0, h, step):
            hits = 0
            for x in range(0, w, step):
                if argb.pixelColor(x, y).alpha() > thresh:
                    hits += 1
                    if hits >= min_hits:
                        if y < mn_y:
                            mn_y = y
                        if y > mx_y:
                            mx_y = y
                        break
        return mn_x, mn_y, mx_x, mx_y

    min_hits = 2 if min(w, h) >= 32 else 1
    min_x, min_y, max_x, max_y = _scan_density(110, min_hits)
    if (max_x - min_x) < max(8, w // 10) or (max_y - min_y) < max(8, h // 10):
        min_x, min_y, max_x, max_y = _scan_density(20, 1)
    if max_x <= min_x or max_y <= min_y:
        return argb
    rx = max(0, min_x)
    ry = max(0, min_y)
    rw = min(w - rx, max_x - min_x + 1)
    rh = min(h - ry, max_y - min_y + 1)
    return argb.copy(rx, ry, rw, rh)


def _extract_uwp_shell_qimage(parsing_name: str, sz: int = 256) -> QImage | None:
    """Extract clean high-res HBITMAP via Win32 IShellItemImageFactory (works for Store/UWP/Xbox & LNKs)."""
    if os.name != "nt" or not parsing_name:
        return None
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32

        class _SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class _BITMAP(ctypes.Structure):
            _fields_ = [
                ("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p),
            ]

        class _BIH(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
            ]

        ole32.CoInitialize(None)
        iid_isif = uuid.UUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}").bytes_le
        shell32.SHCreateItemFromParsingName.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        psi = ctypes.c_void_p()
        hr = shell32.SHCreateItemFromParsingName(parsing_name, None, iid_isif, ctypes.byref(psi))
        if hr != 0 or not psi.value:
            return None

        vtable = ctypes.cast(ctypes.c_void_p.from_address(psi.value), ctypes.POINTER(ctypes.c_void_p))
        release_fn = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        get_img_fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, _SIZE, ctypes.c_int, ctypes.POINTER(wt.HBITMAP))(vtable[3])
        hbmp = wt.HBITMAP()
        try:
            # 0x09 = SIIGBF_ICONONLY | SIIGBF_BIGGERSIZEOK; fallback 0x01 if icon-only unavailable
            hr2 = get_img_fn(psi, _SIZE(sz, sz), 0x09, ctypes.byref(hbmp))
            if hr2 != 0 or not hbmp.value:
                hr2 = get_img_fn(psi, _SIZE(sz, sz), 0x01, ctypes.byref(hbmp))
            if hr2 != 0 or not hbmp.value:
                return None
            bm = _BITMAP()
            gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm))
            w, h = int(bm.bmWidth), int(bm.bmHeight)
            if w <= 0 or h <= 0:
                return None
            bi = _BIH()
            bi.biSize = ctypes.sizeof(bi)
            bi.biWidth = w
            bi.biHeight = -h
            bi.biPlanes = 1
            bi.biBitCount = 32
            bi.biCompression = 0
            buf = ctypes.create_string_buffer(w * h * 4)
            hdc = user32.GetDC(0)
            gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bi), 0)
            user32.ReleaseDC(0, hdc)
            gdi32.DeleteObject(hbmp)
            raw = QImage(buf, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
            cropped = _tight_crop_alpha(raw)
            return cropped if (cropped.width() >= 16 and cropped.height() >= 16) else None
        finally:
            release_fn(psi)
    except Exception:
        return None


def _extract_win32_clean_qimage(path: str) -> QImage | None:
    """Extract high-res icon from EXE/LNK/UWP WITHOUT the Windows shortcut arrow overlay."""
    if os.name != "nt" or not path:
        return None
    if path.lower().startswith("shell:"):
        return _extract_uwp_shell_qimage(path, 256)
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        shell32 = ctypes.windll.shell32
        comctl32 = ctypes.windll.comctl32

        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
        user32.GetIconInfo.argtypes = [wt.HICON, ctypes.c_void_p]
        user32.DestroyIcon.argtypes = [wt.HICON]
        user32.GetDC.argtypes = [wt.HWND]
        user32.GetDC.restype = wt.HDC
        user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        gdi32.GetDIBits.argtypes = [
            wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT
        ]
        shell32.SHGetImageList.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        comctl32.ImageList_GetIcon.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint]
        comctl32.ImageList_GetIcon.restype = wt.HICON

        class _SHFILEINFOW(ctypes.Structure):
            _fields_ = [
                ("hIcon", wt.HICON),
                ("iIcon", ctypes.c_int),
                ("dwAttributes", wt.DWORD),
                ("szDisplayName", wt.WCHAR * 260),
                ("szTypeName", wt.WCHAR * 80),
            ]

        class _ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", wt.BOOL),
                ("xHotspot", wt.DWORD),
                ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP),
                ("hbmColor", wt.HBITMAP),
            ]

        class _BITMAP(ctypes.Structure):
            _fields_ = [
                ("bmType", ctypes.c_long),
                ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", ctypes.c_ushort),
                ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p),
            ]

        class _BIH(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD),
            ]

        shfi = _SHFILEINFOW()
        # SHGFI_SYSICONINDEX (0x4000) intentionally omits SHGFI_LINKOVERLAY (0x8000)
        res = shell32.SHGetFileInfoW(path, 0, ctypes.byref(shfi), ctypes.sizeof(shfi), 0x4000)
        if not res or shfi.iIcon < 0:
            return None

        iid_image_list = uuid.UUID("{46EB5926-582E-4017-9FDF-E8998DAA0950}").bytes_le
        for shil in (4, 2):  # SHIL_JUMBO (256px) -> SHIL_EXTRALARGE (48px)
            ilst = ctypes.c_void_p()
            hr = shell32.SHGetImageList(shil, iid_image_list, ctypes.byref(ilst))
            if hr != 0 or not ilst.value:
                continue
            hicon = comctl32.ImageList_GetIcon(ilst, shfi.iIcon, 0x0001)  # ILD_TRANSPARENT
            if not hicon:
                continue
            try:
                ii = _ICONINFO()
                if not user32.GetIconInfo(hicon, ctypes.byref(ii)) or not ii.hbmColor:
                    continue
                try:
                    bm = _BITMAP()
                    gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
                    w, h = int(bm.bmWidth), int(bm.bmHeight)
                    if w <= 0 or h <= 0:
                        continue
                    bi = _BIH()
                    bi.biSize = ctypes.sizeof(bi)
                    bi.biWidth = w
                    bi.biHeight = -h
                    bi.biPlanes = 1
                    bi.biBitCount = 32
                    bi.biCompression = 0
                    buf = ctypes.create_string_buffer(w * h * 4)
                    hdc = user32.GetDC(0)
                    gdi32.GetDIBits(hdc, ii.hbmColor, 0, h, buf, ctypes.byref(bi), 0)
                    user32.ReleaseDC(0, hdc)
                    raw = QImage(buf, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
                    # Check alpha validity (older 24-bit HICONs have alpha=0 across color bitmap)
                    has_alpha = any(raw.pixelColor(px, py).alpha() > 20 for py in range(0, h, max(1, h // 8)) for px in range(0, w, max(1, w // 8)))
                    if not has_alpha:
                        continue
                    cropped = _tight_crop_alpha(raw)
                    if cropped.width() >= 24 and cropped.height() >= 24:
                        return cropped
                finally:
                    if ii.hbmColor:
                        gdi32.DeleteObject(ii.hbmColor)
                    if ii.hbmMask:
                        gdi32.DeleteObject(ii.hbmMask)
            finally:
                user32.DestroyIcon(hicon)
    except Exception:
        pass
    return _extract_uwp_shell_qimage(path, 256)


def _make_circular_app_icon(src_img: QImage | QPixmap, size: int = 128, bg_hex: str = "#1E293B") -> QIcon:
    """Clip and compose any photo, square tile, squircle, or shaped icon into a true full-bleed circle."""
    img = src_img.toImage() if isinstance(src_img, QPixmap) else src_img
    if img.isNull():
        return QIcon()
    trimmed = _tight_crop_alpha(img)
    tw, th = max(1, trimmed.width()), max(1, trimmed.height())

    def _px_a(fx: float, fy: float) -> int:
        px = max(0, min(tw - 1, int(round((tw - 1) * fx))))
        py = max(0, min(th - 1, int(round((th - 1) * fy))))
        return trimmed.pixelColor(px, py).alpha()

    outer_corners = (_px_a(0.0, 0.0), _px_a(1.0, 0.0), _px_a(0.0, 1.0), _px_a(1.0, 1.0))
    squircle_pts = (_px_a(0.11, 0.11), _px_a(0.89, 0.11), _px_a(0.11, 0.89), _px_a(0.89, 0.89))

    opaque_cnt = 0
    for gy in range(8):
        fy = 0.08 + 0.84 * (gy / 7.0)
        for gx in range(8):
            fx = 0.08 + 0.84 * (gx / 7.0)
            if _px_a(fx, fy) > 140:
                opaque_cnt += 1
    area_fill = opaque_cnt / 64.0
    aspect = min(tw, th) / max(tw, th)

    has_solid_corners = sum(1 for a in outer_corners if a > 180) >= 3
    is_squircle = sum(1 for a in squircle_pts if a > 120) >= 3
    is_full_bleed = (has_solid_corners or is_squircle or area_fill >= 0.72) and (aspect >= 0.68 or has_solid_corners)

    out = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    circ_rect = QRectF(0.5, 0.5, size - 1.0, size - 1.0)
    clip = QPainterPath()
    clip.addEllipse(circ_rect)
    p.save()
    p.setClipPath(clip)

    if is_full_bleed:
        # Rounded squares & squircles get 1.12x bleed so even corner radii are trimmed off into a pure circle
        zoom = 1.12 if (is_squircle and not has_solid_corners) else 1.02
        target_side = max(size, int(round(size * zoom)))
        scaled = trimmed.scaled(
            target_side,
            target_side,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        dx = (size - scaled.width()) * 0.5
        dy = (size - scaled.height()) * 0.5
        p.drawImage(QRectF(dx, dy, scaled.width(), scaled.height()), scaled)
    else:
        bg_col = QColor(bg_hex)
        bg_col.setAlpha(224)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg_col)
        p.drawEllipse(circ_rect)
        inner = int(size * 0.68)
        scaled = trimmed.scaled(
            inner,
            inner,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        dx = (size - scaled.width()) * 0.5
        dy = (size - scaled.height()) * 0.5
        p.drawImage(QRectF(dx, dy, scaled.width(), scaled.height()), scaled)

    # Subtle circular glass rim inside clip
    p.setPen(QPen(QColor(255, 255, 255, 72), max(1.4, size * 0.022)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(circ_rect.adjusted(0.8, 0.8, -0.8, -0.8))
    p.restore()
    p.end()

    qi = QIcon()
    qi.addPixmap(QPixmap.fromImage(out))
    return qi


class _AtollAppBubble(QPushButton):
    """Atoll-style pure circular dock icon with fixed layout slot, true ellipse clip, and smooth centered zoom."""

    def __init__(self, tooltip: str, accent_hex: str = "#38BDF8", is_add: bool = False, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._slot_sz = 76 if is_add else 78
        self._base_sz = 60 if is_add else 66
        self._max_sz = 68 if is_add else 74
        self._cur_sz = float(self._base_sz)
        self._hover_t = 0.0
        self._accent_hex = accent_hex
        self._is_add = is_add
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("border: none; background: transparent; padding: 0px; margin: 0px;")
        self.setFixedSize(self._slot_sz, self._slot_sz)
        self.setIconSize(QSize(self._max_sz, self._max_sz))
        self._apply_size(float(self._base_sz), hover_t=0.0)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(145)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_val)

    def _on_anim_val(self, val: object) -> None:
        sz = float(val)  # type: ignore[arg-type]
        span = max(1.0, float(self._max_sz - self._base_sz))
        t = max(0.0, min(1.0, (sz - self._base_sz) / span))
        self._apply_size(sz, hover_t=t)

    def _apply_size(self, sz: float, hover_t: float) -> None:
        self._cur_sz = sz
        self._hover_t = hover_t
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            return
        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        ico = self.icon()
        cache_key = (
            w,
            h,
            round(dpr, 2),
            round(self._cur_sz, 1),
            round(self._hover_t, 2),
            self._accent_hex,
            self._is_add,
            ico.cacheKey() if not ico.isNull() else 0,
        )
        cached = getattr(self, "_pm_cache", None)
        if cached is not None and cached[0] == cache_key:
            p = QPainter(self)
            p.drawPixmap(0, 0, cached[1])
            p.end()
            return

        pw = max(1, int(round(w * dpr)))
        ph = max(1, int(round(h * dpr)))
        sprite = QPixmap(pw, ph)
        sprite.setDevicePixelRatio(dpr)
        sprite.fill(Qt.GlobalColor.transparent)

        wf, hf = float(w), float(h)
        d = max(4.0, min(min(wf, hf) - 2.0, self._cur_sz))
        cx, cy = wf * 0.5, hf * 0.5
        circ = QRectF(cx - d * 0.5, cy - d * 0.5, d, d)

        p = QPainter(sprite)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        clip = QPainterPath()
        clip.addEllipse(circ)

        ac = QColor(self._accent_hex)
        border_a = int(95 + 150 * self._hover_t)
        bg_a = int(28 + 36 * self._hover_t) if self._is_add else int(18 + 22 * self._hover_t)

        p.save()
        p.setClipPath(clip)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, bg_a))
        p.drawEllipse(circ)

        if not ico.isNull():
            if self._is_add:
                isz = int(round(d * 0.48))
                pm = ico.pixmap(isz, isz)
                if not pm.isNull():
                    p_dpr = max(1.0, float(pm.devicePixelRatio()))
                    ipw, iph = pm.width() / p_dpr, pm.height() / p_dpr
                    p.drawPixmap(QRectF(cx - ipw * 0.5, cy - iph * 0.5, ipw, iph), pm, QRectF(0, 0, pm.width(), pm.height()))
            else:
                req = int(round(d * 2.0))
                pm = ico.pixmap(req, req)
                if not pm.isNull():
                    p.drawPixmap(circ, pm, QRectF(0, 0, pm.width(), pm.height()))
        p.restore()

        pen = QPen(QColor(ac.red(), ac.green(), ac.blue(), border_a), 1.5)
        if self._is_add:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(circ.adjusted(0.75, 0.75, -0.75, -0.75))
        p.end()

        self._pm_cache = (cache_key, sprite)
        out_p = QPainter(self)
        out_p.drawPixmap(0, 0, sprite)
        out_p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(self._cur_sz)
        self._anim.setEndValue(float(self._max_sz))
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._cur_sz)
        self._anim.setEndValue(float(self._base_sz))
        self._anim.start()
        super().leaveEvent(event)

    def set_accent(self, accent_hex: str) -> None:
        self._accent_hex = accent_hex
        self._apply_size(self._cur_sz, hover_t=self._hover_t)


def _make_plus_orb_icon(color_hex: str = "#F2F5FA") -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color_hex), 4.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(32, 16, 32, 48)
    p.drawLine(16, 32, 48, 32)
    p.end()
    return QIcon(pm)


def resolve_launcher_qicon(
    item: LauncherItem,
    cache_dir: Path,
    provider: QFileIconProvider,
    icon_fg: str = "#F2F5FA",
    accent: str = "#38BDF8",
) -> tuple[QIcon, bool]:
    """Return (circular arrow-free QIcon for app/site, needs_favicon_fetch)."""
    if item.icon_path:
        ip = Path(item.icon_path)
        if ip.is_file():
            img = QImage(str(ip))
            if not img.isNull():
                return _make_circular_app_icon(img), False

    tgt = (item.target or "").strip()
    if item.kind == "url" or tgt.startswith(("http://", "https://")):
        cached = _favicon_cache_path(tgt, cache_dir)
        if cached.is_file():
            img = QImage(str(cached))
            if not img.isNull():
                return _make_circular_app_icon(img), False
        return _make_circular_app_icon(get_pixmap("app", size=72, color=icon_fg, accent=accent)), True

    if tgt:
        clean_img = _extract_win32_clean_qimage(tgt)
        if clean_img is not None and not clean_img.isNull():
            return _make_circular_app_icon(clean_img), False
        tp = Path(tgt)
        if tp.exists():
            qi = provider.icon(QFileInfo(str(tp)))
            if not qi.isNull():
                pm = qi.pixmap(96, 96)
                if not pm.isNull():
                    return _make_circular_app_icon(pm), False

    return _make_circular_app_icon(get_pixmap("app", size=72, color=icon_fg, accent=accent)), False


@dataclass
class InstalledAppEntry:
    title: str
    target: str
    kind: str  # "uwp" or "app"
    source_tag: str  # "Microsoft Store", "Меню Пуск", "Рабочий стол"
    icon_hint: str = ""


_INSTALLED_APPS_CACHE: list[InstalledAppEntry] = []
_INSTALLED_APPS_LOCK = threading.Lock()


def _parse_start_apps_json(raw: str) -> list[InstalledAppEntry]:
    entries: list[InstalledAppEntry] = []
    text = (raw or "").strip()
    if not text:
        return entries
    try:
        data = json.loads(text)
    except Exception:
        return entries
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return entries
    for row in data:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        app_id = str(row.get("AppID") or "").strip()
        if not name or not app_id:
            continue
        target = f"shell:AppsFolder\\{app_id}"
        is_uwp = ("!" in app_id) or ("_" in app_id)
        entries.append(
            InstalledAppEntry(
                title=name,
                target=target,
                kind="uwp" if is_uwp else "app",
                source_tag="Microsoft Store" if is_uwp else "Приложения",
                icon_hint=target,
            )
        )
    return entries


def _fetch_start_apps_powershell() -> list[InstalledAppEntry]:
    ps_cmd = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
    )
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creationflags,
        )
        if res.returncode == 0 and res.stdout:
            return _parse_start_apps_json(res.stdout)
    except Exception:
        pass
    return []


def _scan_shortcut_folders() -> list[InstalledAppEntry]:
    roots: list[tuple[Path, str]] = []
    prog_data = os.environ.get("ProgramData")
    appdata = os.environ.get("APPDATA")
    if prog_data:
        roots.append((Path(prog_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs", "Меню Пуск"))
    if appdata:
        roots.append((Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs", "Меню Пуск"))
    roots.append((Path.home() / "Desktop", "Рабочий стол"))

    skip_words = ("uninstall", "удалить", "удаление", "деинсталл", "readme", "help", "справка", "manual")
    entries: list[InstalledAppEntry] = []
    for root, tag in roots:
        if not root.exists():
            continue
        try:
            for p in root.rglob("*.lnk"):
                stem = p.stem.strip()
                if not stem:
                    continue
                low = stem.lower()
                if any(w in low for w in skip_words):
                    continue
                entries.append(
                    InstalledAppEntry(
                        title=stem,
                        target=str(p),
                        kind="app",
                        source_tag=tag,
                        icon_hint=str(p),
                    )
                )
        except Exception:
            continue
    return entries


def scan_installed_apps(force: bool = False) -> list[InstalledAppEntry]:
    with _INSTALLED_APPS_LOCK:
        if _INSTALLED_APPS_CACHE and not force:
            return list(_INSTALLED_APPS_CACHE)

    by_key: dict[str, InstalledAppEntry] = {}
    for entry in _fetch_start_apps_powershell():
        key = entry.title.casefold()
        existing = by_key.get(key)
        if existing is None or (entry.kind == "uwp" and existing.kind != "uwp"):
            by_key[key] = entry

    for entry in _scan_shortcut_folders():
        key = entry.title.casefold()
        if key not in by_key:
            by_key[key] = entry

    result = sorted(by_key.values(), key=lambda e: e.title.casefold())
    with _INSTALLED_APPS_LOCK:
        _INSTALLED_APPS_CACHE.clear()
        _INSTALLED_APPS_CACHE.extend(result)
    return list(result)


class _InstalledAppsDialog(QDialog):
    """Modal picker listing installed system & Microsoft Store apps with circular icons."""

    _scan_ready = pyqtSignal(list)

    def __init__(
        self,
        parent=None,
        entries: list[InstalledAppEntry] | None = None,
        accent_hex: str = "#60A5FA",
        icon_fg: str = "#F2F5FA",
    ) -> None:
        super().__init__(parent)
        self._accent_hex = accent_hex
        self._icon_fg = icon_fg
        self._all_entries: list[InstalledAppEntry] = list(entries or [])
        self._icon_cache: dict[str, QIcon] = {}
        self._fallback_icon = _make_circular_app_icon(
            get_pixmap("app", size=48, color=icon_fg, accent=accent_hex),
            size=68,
            bg_hex="#1E293B",
        )
        self._pending_rows: list[tuple[int, InstalledAppEntry]] = []
        self._closed = False
        self.selected_entry: InstalledAppEntry | None = None
        self.chosen_action: str = ""

        self.setWindowTitle("Добавить приложение в лаунчер")
        self.resize(460, 520)
        self.setStyleSheet(
            "QDialog { background: rgba(18, 22, 32, 245); color: #F2F5FA; }"
            "QLabel { color: #F2F5FA; background: transparent; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hdr = QLabel("Установленные приложения")
        hdr.setStyleSheet("font-size: 16px; font-weight: 800; color: #F8FAFC;")
        root.addWidget(hdr)

        sub = QLabel("Выберите приложение, чтобы добавить круглую иконку в лаунчер")
        sub.setStyleSheet("font-size: 12px; color: rgba(203, 213, 225, 0.78);")
        root.addWidget(sub)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Поиск по названию (Xbox, Telegram, Chrome…)")
        self.search_edit.setFixedHeight(36)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setStyleSheet(
            "QLineEdit { background: rgba(255,255,255,0.09); color: #F8FAFC; "
            "border: 1px solid rgba(255,255,255,0.14); border-radius: 18px; "
            "padding: 0px 14px; font-size: 13px; }"
            "QLineEdit:focus { border: 1px solid rgba(96,165,250,0.7); }"
        )
        self.search_edit.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_edit, 1)

        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setToolTip("Пересканировать установленные приложения")
        self.refresh_btn.setFixedSize(36, 36)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,0.09); color: #F8FAFC; "
            "border: 1px solid rgba(255,255,255,0.14); border-radius: 18px; "
            "font-size: 16px; font-weight: 700; }"
            "QPushButton:hover { background: rgba(255,255,255,0.18); }"
        )
        self.refresh_btn.clicked.connect(lambda: self._start_scan(force=True))
        search_row.addWidget(self.refresh_btn)
        root.addLayout(search_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 11px; color: rgba(148, 163, 184, 0.9);")
        root.addWidget(self.status_lbl)

        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(34, 34))
        self.list_widget.setSpacing(3)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setStyleSheet(
            "QListWidget { background: rgba(255,255,255,0.04); "
            "border: 1px solid rgba(255,255,255,0.10); border-radius: 18px; padding: 6px; "
            "color: #F2F5FA; outline: none; }"
            "QListWidget::item { padding: 6px 10px; border-radius: 14px; min-height: 40px; }"
            "QListWidget::item:hover { background: rgba(255,255,255,0.09); }"
            "QListWidget::item:selected { background: rgba(96,165,250,0.28); color: #FFFFFF; }"
        )
        self.list_widget.itemDoubleClicked.connect(lambda _it: self._accept_selected())
        self.list_widget.itemSelectionChanged.connect(self._sync_add_btn)
        root.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        sec_style = (
            "QPushButton { background: rgba(255,255,255,0.08); color: #E2E8F0; "
            "border: 1px solid rgba(255,255,255,0.12); border-radius: 18px; "
            "padding: 0px 14px; font-size: 12px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )
        self.url_btn = QPushButton("Сайт")
        self.url_btn.setIcon(get_icon("globe", 14, "#E2E8F0", "#60A5FA"))
        self.url_btn.setFixedHeight(36)
        self.url_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.url_btn.setStyleSheet(sec_style)
        self.url_btn.clicked.connect(lambda: self._finish_action("url"))
        btn_row.addWidget(self.url_btn)

        self.file_btn = QPushButton("Файл…")
        self.file_btn.setIcon(get_icon("folder", 14, "#E2E8F0", "#60A5FA"))
        self.file_btn.setFixedHeight(36)
        self.file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.file_btn.setStyleSheet(sec_style)
        self.file_btn.clicked.connect(lambda: self._finish_action("file"))
        btn_row.addWidget(self.file_btn)

        btn_row.addStretch(1)

        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.setFixedHeight(36)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(sec_style)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.add_btn = QPushButton("Добавить")
        self.add_btn.setFixedHeight(36)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.setStyleSheet(
            f"QPushButton {{ background: {accent_hex}; color: #0B1120; border: none; "
            f"border-radius: 18px; padding: 0px 18px; font-size: 12px; font-weight: 800; }}"
            f"QPushButton:hover {{ background: #93C5FD; }}"
            f"QPushButton:disabled {{ background: rgba(255,255,255,0.10); color: rgba(255,255,255,0.35); }}"
        )
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._accept_selected)
        btn_row.addWidget(self.add_btn)
        root.addLayout(btn_row)

        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(12)
        self._icon_timer.timeout.connect(self._load_icon_batch)
        self._scan_ready.connect(self._on_scan_finished)
        self.finished.connect(self._on_dialog_finished)

        if entries is not None:
            self._apply_filter()
        else:
            with _INSTALLED_APPS_LOCK:
                cached = list(_INSTALLED_APPS_CACHE)
            if cached:
                self._all_entries = cached
                self._apply_filter()
            else:
                self._start_scan(force=False)

    def _on_dialog_finished(self) -> None:
        self._closed = True
        self._icon_timer.stop()

    def _start_scan(self, force: bool = False) -> None:
        self.refresh_btn.setEnabled(False)
        self.status_lbl.setText("Сканирование установленных приложений…")

        def _worker() -> None:
            apps = scan_installed_apps(force=force)
            if not self._closed:
                self._scan_ready.emit(apps)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_scan_finished(self, apps: list) -> None:
        if self._closed:
            return
        self.refresh_btn.setEnabled(True)
        self._all_entries = list(apps)
        self._apply_filter()

    def _apply_filter(self) -> None:
        self._icon_timer.stop()
        self._pending_rows.clear()
        self.list_widget.clear()

        query = self.search_edit.text().strip().casefold()
        if query:
            matched = [
                e for e in self._all_entries
                if query in e.title.casefold() or query in e.source_tag.casefold()
            ]
        else:
            matched = self._all_entries

        for idx, entry in enumerate(matched):
            item = QListWidgetItem(f"{entry.title}   ·   {entry.source_tag}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            cached_ico = self._icon_cache.get(entry.target)
            if cached_ico is not None:
                item.setIcon(cached_ico)
            else:
                item.setIcon(self._fallback_icon)
                self._pending_rows.append((idx, entry))
            self.list_widget.addItem(item)

        total = len(self._all_entries)
        shown = len(matched)
        if total == 0:
            self.status_lbl.setText("Приложения не найдены — нажмите ↻ или выберите файл вручную")
        elif query:
            self.status_lbl.setText(f"Найдено {shown} из {total} приложений")
        else:
            self.status_lbl.setText(f"Найдено приложений: {total}")

        if matched:
            self.list_widget.setCurrentRow(0)
        self._sync_add_btn()
        if self._pending_rows:
            self._icon_timer.start()

    def _load_icon_batch(self) -> None:
        if self._closed:
            self._icon_timer.stop()
            return
        loaded = 0
        while self._pending_rows and loaded < 4:
            row_idx, entry = self._pending_rows.pop(0)
            item = self.list_widget.item(row_idx)
            if item is None or item.data(Qt.ItemDataRole.UserRole) is not entry:
                continue
            ico = self._icon_cache.get(entry.target)
            if ico is None:
                qimg = _extract_win32_clean_qimage(entry.icon_hint or entry.target)
                if qimg is not None and not qimg.isNull():
                    ico = _make_circular_app_icon(qimg, size=68, bg_hex="#1E293B")
                else:
                    ico = self._fallback_icon
                self._icon_cache[entry.target] = ico
            item.setIcon(ico)
            loaded += 1
        if not self._pending_rows:
            self._icon_timer.stop()

    def _sync_add_btn(self) -> None:
        self.add_btn.setEnabled(self.list_widget.currentItem() is not None)

    def _accept_selected(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, InstalledAppEntry):
            self.selected_entry = entry
            self.chosen_action = "app"
            self.accept()

    def _finish_action(self, action: str) -> None:
        self.chosen_action = action
        self.accept()


class _AddShortcutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, *, url_mode: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить сайт" if url_mode else tr("add_shortcut"))
        self.setMinimumWidth(340)
        form = QFormLayout(self)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("YouTube / ChatGPT / Docs" if url_mode else "Telegram / Steam / Docs")
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText(
            "https://example.com" if url_mode else r"C:\Program Files\... или https://..."
        )
        browse_btn = QPushButton("Обзор…")
        browse_btn.clicked.connect(self._browse)
        browse_btn.setVisible(not url_mode)

        t_row = QHBoxLayout()
        t_row.addWidget(self.target_edit, 1)
        t_row.addWidget(browse_btn)

        self.group_edit = QLineEdit()
        self.group_edit.setPlaceholderText("Необязательная папка/группа")
        self.icon_edit = QLineEdit()

        form.addRow("Название:", self.name_edit)
        form.addRow("Адрес (URL):" if url_mode else "Файл / URL:", t_row)
        form.addRow("Группа:", self.group_edit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _browse(self) -> None:
        start_dir = os.environ.get("ProgramFiles") or str(Path.home() / "Desktop")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите программу в Проводнике",
            start_dir,
            "Программы и ярлыки (*.exe *.lnk *.bat *.cmd *.url);;Все файлы (*.*)",
        )
        if path:
            self.target_edit.setText(path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(path).stem)


class _AtollCircleToggle(QWidget):
    """Atoll obsidian rounded-square quick-toggle tile with top-right ⋮ menu button."""

    clicked = pyqtSignal()
    menu_clicked = pyqtSignal()

    def __init__(
        self,
        icon_name: str,
        title: str,
        active_hex: str = "#2563EB",
        has_menu: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.icon_name = icon_name
        self.title = title
        self.active_hex = active_hex
        self._has_menu = bool(has_menu)
        self._active = False
        self._hovered = False
        self._menu_hovered = False
        self._menu_rect = QRectF()
        self._status_text = "Выкл"
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(0, 96)
        self.setFixedHeight(96)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(title)

    def set_state(self, active: bool, status_text: str | None = None, *, accent_hex: str | None = None) -> None:
        changed = (self._active != bool(active))
        self._active = bool(active)
        if accent_hex and accent_hex != self.active_hex:
            self.active_hex = accent_hex
            changed = True
        if status_text is not None and status_text != self._status_text:
            self._status_text = status_text
            changed = True
        elif status_text is None:
            new_st = "Вкл" if self._active else "Выкл"
            if new_st != self._status_text:
                self._status_text = new_st
                changed = True
        if changed:
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._menu_hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        mh = bool(self._has_menu and self._menu_rect.contains(QPointF(event.pos())))
        if mh != self._menu_hovered:
            self._menu_hovered = mh
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            if self._has_menu and self._menu_rect.contains(QPointF(event.pos())):
                self.menu_clicked.emit()
            else:
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        wi, hi = self.width(), self.height()
        if wi <= 2 or hi <= 2:
            return
        w = float(wi)
        bw = min(56.0, max(42.0, w - 6.0))
        bh = 52.0
        cx = w * 0.5
        c_rect = QRectF(cx - bw * 0.5, 3.0, bw, bh)
        if self._has_menu:
            mr = QRectF(c_rect.right() - 19.0, c_rect.top() + 3.0, 16.0, 16.0)
            self._menu_rect = mr.adjusted(-2.0, -2.0, 2.0, 2.0)
        else:
            mr = QRectF()
            self._menu_rect = QRectF()

        dpr = max(1.0, float(self.devicePixelRatioF() or 1.0))
        cache_key = (
            wi,
            hi,
            round(dpr, 2),
            self._active,
            self._hovered,
            self._menu_hovered,
            self.active_hex,
            self._status_text,
            self.title,
            self.icon_name,
            self._has_menu,
        )
        cached = getattr(self, "_tile_pm_cache", None)
        if cached is not None and cached[0] == cache_key:
            p = QPainter(self)
            p.drawPixmap(0, 0, cached[1])
            p.end()
            return

        pw = max(1, int(round(wi * dpr)))
        ph = max(1, int(round(hi * dpr)))
        sprite = QPixmap(pw, ph)
        sprite.setDevicePixelRatio(dpr)
        sprite.fill(Qt.GlobalColor.transparent)
        p = QPainter(sprite)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        ac = QColor(self.active_hex)
        if self._active:
            halo = QColor(ac.red(), ac.green(), ac.blue(), 66 if self._hovered else 48)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawRoundedRect(c_rect.adjusted(-3.0, -3.0, 3.0, 3.0), 17.0, 17.0)
            fill = ac.lighter(110) if self._hovered else ac
            rim = QColor(255, 255, 255, 125)
        else:
            fill = QColor(36, 42, 56, 205) if self._hovered else QColor(24, 28, 38, 185)
            rim = QColor(255, 255, 255, 58 if self._hovered else 30)

        p.setPen(QPen(rim, 1.1))
        p.setBrush(fill)
        p.drawRoundedRect(c_rect, 15.0, 15.0)

        icon_sz = max(26, int(round(min(bw, bh) * 0.54)))
        fg = "#FFFFFF" if self._active else "#E2E8F0"
        pm = get_pixmap(self.icon_name, size=icon_sz, color=fg, accent="#FFFFFF" if self._active else self.active_hex)
        if not pm.isNull():
            iy = c_rect.center().y() - icon_sz * 0.5
            target = QRectF(cx - icon_sz * 0.5, iy, float(icon_sz), float(icon_sz))
            p.drawPixmap(target, pm, QRectF(0.0, 0.0, float(pm.width()), float(pm.height())))

        if self._has_menu:
            p.setPen(Qt.PenStyle.NoPen)
            dot_bg = QColor(255, 255, 255, 58 if self._menu_hovered else (34 if self._hovered else 18))
            p.setBrush(dot_bg)
            p.drawEllipse(mr)
            p.setBrush(QColor(255, 255, 255, 225 if self._menu_hovered else 175))
            mcx = mr.center().x()
            mcy = mr.center().y()
            for dy in (-3.2, 0.0, 3.2):
                p.drawEllipse(QPointF(mcx, mcy + dy), 1.15, 1.15)

        f = self.font()
        f.setPixelSize(10)
        f.setBold(self._active)
        p.setFont(f)
        p.setPen(QColor("#F8FAFC" if self._active else "#CBD5E1"))
        max_tw = max(24, int(w - 2))
        t_elided = p.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, max_tw)
        p.drawText(QRectF(1.0, 58.0, max(1.0, w - 2.0), 16.0), Qt.AlignmentFlag.AlignCenter, t_elided)

        f2 = self.font()
        f2.setPixelSize(9)
        f2.setBold(False)
        p.setFont(f2)
        sub_col = ac.lighter(135) if self._active else QColor("#64748B")
        p.setPen(sub_col)
        s_elided = p.fontMetrics().elidedText(self._status_text, Qt.TextElideMode.ElideRight, max_tw)
        p.drawText(QRectF(1.0, 74.0, max(1.0, w - 2.0), 15.0), Qt.AlignmentFlag.AlignCenter, s_elided)
        p.end()

        self._tile_pm_cache = (cache_key, sprite)
        out_p = QPainter(self)
        out_p.drawPixmap(0, 0, sprite)
        out_p.end()


class _AtollTelemetryCard(QFrame):
    """Atoll dark-glass telemetry card (CPU / GPU / Memory, no sliders)."""

    def __init__(self, title: str, color_hex: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color_hex = color_hex
        self.setObjectName("Card")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setStyleSheet("_AtollTelemetryCard { border-radius: 13px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet(f"color: {color_hex}; font-size: 9px; font-weight: 800; letter-spacing: 0.7px; background: transparent;")
        self.val_lbl = QLabel("0%")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.val_lbl.setStyleSheet("color: #F8FAFC; font-size: 11px; font-weight: 700; background: transparent;")
        top.addWidget(self.title_lbl, 1)
        top.addWidget(self.val_lbl)
        lay.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: rgba(255,255,255,0.10); border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {color_hex}; border-radius: 3px; }}"
        )
        lay.addWidget(self.bar)

        self.sub_lbl = QLabel("—")
        self.sub_lbl.setMinimumWidth(0)
        self.sub_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.sub_lbl.setStyleSheet("color: #94A3B8; font-size: 9px; background: transparent;")
        self.sub_lbl.setWordWrap(False)
        self._raw_sub = "—"
        lay.addWidget(self.sub_lbl)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        w = max(40, self.width() - 20)
        self.sub_lbl.setText(self.sub_lbl.fontMetrics().elidedText(self._raw_sub, Qt.TextElideMode.ElideRight, w))

    def set_metrics(self, pct: float, value_text: str, sub_text: str, *, title_text: str | None = None) -> None:
        pct_i = max(0, min(100, int(round(pct))))
        sig = (pct_i, value_text, sub_text, title_text, self.width())
        if getattr(self, "_last_metric_sig", None) == sig:
            return
        self._last_metric_sig = sig
        if title_text is not None and self.title_lbl.text() != title_text:
            self.title_lbl.setText(title_text)
        if self.bar.value() != pct_i:
            self.bar.setValue(pct_i)
        if self.val_lbl.text() != value_text:
            self.val_lbl.setText(value_text)
        if getattr(self, "_raw_sub", None) != sub_text or getattr(self, "_last_sub_w", -1) != self.width():
            self._raw_sub = sub_text
            self._last_sub_w = self.width()
            self.sub_lbl.setToolTip(sub_text)
            w = max(40, self.width() - 20)
            self.sub_lbl.setText(self.sub_lbl.fontMetrics().elidedText(sub_text, Qt.TextElideMode.ElideRight, w))


class _AtollControlHub(QFrame):
    """Atoll-style Control Center: 6 rounded-square radios + inline sub-menus, 4 power pills, 3 telemetry bars, Discord & DND."""

    control_toggled = pyqtSignal(str)
    power_requested = pyqtSignal(str)
    discord_action = pyqtSignal(str)
    dnd_requested = pyqtSignal(int)
    wifi_scan_ready = pyqtSignal(list)
    bt_scan_ready = pyqtSignal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(425)
        self._accent_hex = resolve_time_accent()
        self._icon_fg = "#F2F5FA"
        self._power_armed: str = ""
        self._open_menu_key: str = ""
        self._last_wifi_ssid: str = ""
        self._last_bt_devs: list = []
        self._last_bt_active: bool = False
        self._bt_scan_busy: bool = False
        self._last_vpn_active: bool = False
        self._last_vpn_name: str = ""
        self._wifi_selected_ssid: str = ""
        self._power_reset_timer = QTimer(self)
        self._power_reset_timer.setSingleShot(True)
        self._power_reset_timer.timeout.connect(self._disarm_power)
        self.wifi_scan_ready.connect(self._on_wifi_scan_ready)
        self.bt_scan_ready.connect(self._on_bt_scan_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 9, 10, 10)
        root.setSpacing(9)

        # Header + laptop-only battery badge
        hdr_row = QHBoxLayout()
        self.ctrl_hdr_lbl = QLabel(tr("control_center").upper())
        self.ctrl_hdr_lbl.setStyleSheet("color: #94A3B8; font-size: 10px; font-weight: 800; letter-spacing: 0.9px; background: transparent;")
        self.batt_badge = QLabel("")
        self.batt_badge.setStyleSheet(
            "color: #4ADE80; background: rgba(34, 197, 94, 34); border: 1px solid rgba(34, 197, 94, 90); border-radius: 8px; padding: 1px 7px; font-size: 10px; font-weight: 700;"
        )
        self.batt_badge.hide()
        hdr_row.addWidget(self.ctrl_hdr_lbl)
        hdr_row.addStretch(1)
        hdr_row.addWidget(self.batt_badge)
        root.addLayout(hdr_row)

        # Row 1: 6 Circular Toggles (Wi-Fi, Bluetooth, Режим полёта, VPN, Чистый стол, Синий свет)
        circles_row = QHBoxLayout()
        circles_row.setContentsMargins(0, 0, 0, 0)
        circles_row.setSpacing(3)
        self.radio_toggles: dict[str, _AtollCircleToggle] = {}
        radio_defs = [
            ("wifi", "wifi", tr("wifi"), "#2563EB"),
            ("bt", "bluetooth", tr("bluetooth"), "#3B82F6"),
            ("airplane", "plane", tr("airplane_mode"), "#F59E0B"),
            ("vpn", "vpn", tr("vpn"), "#22C55E"),
            ("desktop", "desktop", tr("clean_desktop"), "#10B981"),
            ("night_light", "sun", "Синий свет", "#F59E0B"),
        ]
        for key, ico, label, col_hex in radio_defs:
            tile = _AtollCircleToggle(ico, label, active_hex=col_hex, has_menu=(key != "airplane"), parent=self)
            tile.clicked.connect(lambda k=key: self.control_toggled.emit(k))
            tile.menu_clicked.connect(lambda k=key: self._toggle_inline_menu(k))
            self.radio_toggles[key] = tile
            circles_row.addWidget(tile, 1)
        root.addLayout(circles_row)

        # Collapsible Inline Sub-Panel for ⋮ corner menu (Wi-Fi list + password, Bluetooth devices, VPN/Quick info)
        self.inline_subpanel = QFrame()
        self.inline_subpanel.setObjectName("Surface")
        self.inline_subpanel.setStyleSheet("QFrame#Surface { border-radius: 12px; }")
        sp_lay = QVBoxLayout(self.inline_subpanel)
        sp_lay.setContentsMargins(8, 6, 8, 8)
        sp_lay.setSpacing(5)
        sp_hdr = QHBoxLayout()
        sp_hdr.setSpacing(6)
        self.sub_title_lbl = QLabel("")
        self.sub_title_lbl.setStyleSheet("font-weight: 700; font-size: 11px;")
        self.sub_refresh_btn = QPushButton("")
        self.sub_refresh_btn.setIconSize(QSize(14, 14))
        self.sub_refresh_btn.setToolTip("Обновить")
        self.sub_refresh_btn.setFixedSize(26, 26)
        self.sub_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sub_refresh_btn.clicked.connect(lambda _=False: self._refresh_inline_menu())
        self.sub_os_btn = QPushButton("Параметры")
        self.sub_os_btn.setFixedHeight(26)
        self.sub_os_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sub_os_btn.clicked.connect(self._open_subpanel_os_settings)
        self.sub_close_btn = QPushButton("")
        self.sub_close_btn.setIconSize(QSize(12, 12))
        self.sub_close_btn.setToolTip("Закрыть")
        self.sub_close_btn.setFixedSize(26, 26)
        self.sub_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sub_close_btn.clicked.connect(lambda _=False: self._toggle_inline_menu(""))
        self._style_subpanel_buttons(is_dark=True, fg="#F2F5FA", accent="#60A5FA")
        sp_hdr.addWidget(self.sub_title_lbl, 1)
        sp_hdr.addWidget(self.sub_refresh_btn)
        sp_hdr.addWidget(self.sub_os_btn)
        sp_hdr.addWidget(self.sub_close_btn)
        sp_lay.addLayout(sp_hdr)

        self.sub_list = QListWidget()
        self.sub_list.setMaximumHeight(96)
        self.sub_list.itemClicked.connect(self._on_sub_list_clicked)
        sp_lay.addWidget(self.sub_list)

        self.wifi_auth_row = QWidget()
        wa_lay = QHBoxLayout(self.wifi_auth_row)
        wa_lay.setContentsMargins(0, 0, 0, 0)
        wa_lay.setSpacing(5)
        self.wifi_pwd_edit = QLineEdit()
        self.wifi_pwd_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.wifi_pwd_edit.setPlaceholderText("Пароль Wi-Fi…")
        self.wifi_pwd_edit.returnPressed.connect(self._connect_selected_wifi)
        self.wifi_conn_btn = QPushButton("Подключить")
        self.wifi_conn_btn.setObjectName("Primary")
        self.wifi_conn_btn.setFixedHeight(26)
        self.wifi_conn_btn.clicked.connect(self._connect_selected_wifi)
        wa_lay.addWidget(self.wifi_pwd_edit, 1)
        wa_lay.addWidget(self.wifi_conn_btn)
        self.wifi_auth_row.hide()
        sp_lay.addWidget(self.wifi_auth_row)

        self.sub_status_lbl = QLabel("")
        self.sub_status_lbl.setObjectName("Muted")
        self.sub_status_lbl.setWordWrap(True)
        sp_lay.addWidget(self.sub_status_lbl)

        self.inline_subpanel.hide()
        root.addWidget(self.inline_subpanel)

        # Row 2: 4 Power Action Buttons (lock, sleep, restart, shutdown)
        pwr_row = QHBoxLayout()
        pwr_row.setSpacing(6)
        self.power_btns: dict[str, QPushButton] = {}
        self._power_labels: dict[str, str] = {
            "lock": tr("power_lock"),
            "sleep": tr("power_sleep"),
            "restart": tr("power_restart"),
            "shutdown": tr("power_shutdown"),
        }
        self._power_icons: dict[str, str] = {
            "lock": "lock",
            "sleep": "sleep",
            "restart": "restart",
            "shutdown": "power",
        }
        for key in ("lock", "sleep", "restart", "shutdown"):
            btn = QPushButton(self._power_labels[key])
            btn.setFixedHeight(32)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.setIcon(get_icon(self._power_icons[key], size=15, color="#E2E8F0", accent="#F87171" if key == "shutdown" else "#93C5FD"))
            btn.setIconSize(QSize(15, 15))
            btn.setStyleSheet("QPushButton { border-radius: 10px; font-size: 10px; font-weight: 600; padding: 0 4px; }")
            btn.setToolTip(self._power_labels[key])
            btn.clicked.connect(lambda _, k=key: self._on_power_clicked(k))
            self.power_btns[key] = btn
            pwr_row.addWidget(btn, 1)
        root.addLayout(pwr_row)

        # Row 3: 3 Telemetry Cards (CPU Blue, GPU Green, Memory Purple)
        telem_row = QHBoxLayout()
        telem_row.setSpacing(5)
        self.cpu_card = _AtollTelemetryCard(tr("cpu_title").upper(), "#3B82F6", parent=self)
        self.gpu_card = _AtollTelemetryCard(tr("gpu_title").upper(), "#22C55E", parent=self)
        self.ram_card = _AtollTelemetryCard(tr("memory_title").upper(), "#A855F7", parent=self)
        telem_row.addWidget(self.cpu_card, 1)
        telem_row.addWidget(self.gpu_card, 1)
        telem_row.addWidget(self.ram_card, 1)
        root.addLayout(telem_row)

        # Row 4 & 5: Full-width compact bars for Discord voice and Do Not Disturb (zero button clipping)
        discord_card = QFrame()
        discord_card.setObjectName("Card")
        discord_card.setMinimumWidth(0)
        discord_card.setStyleSheet("QFrame#Card { border-radius: 12px; }")
        self.discord_card = discord_card
        dc_lay = QHBoxLayout(discord_card)
        dc_lay.setContentsMargins(10, 6, 10, 6)
        dc_lay.setSpacing(6)
        dc_hdr = QVBoxLayout()
        dc_hdr.setSpacing(0)
        d_title = QLabel(tr("discord_voice"))
        d_title.setStyleSheet("color: #818CF8; font-size: 10px; font-weight: 800; background: transparent;")
        self.discord_status_lbl = QLabel("Готово")
        self.discord_status_lbl.setStyleSheet("color: #94A3B8; font-size: 9px; background: transparent;")
        dc_hdr.addWidget(d_title)
        dc_hdr.addWidget(self.discord_status_lbl)
        dc_hdr_w = QWidget()
        dc_hdr_w.setFixedWidth(96)
        dc_hdr_w.setStyleSheet("background: transparent;")
        dc_hdr_w.setLayout(dc_hdr)
        dc_lay.addWidget(dc_hdr_w)

        dc_btns = QHBoxLayout()
        dc_btns.setSpacing(5)
        self.dc_mute_btn = QPushButton(tr("discord_mute"))
        self.dc_deaf_btn = QPushButton(tr("discord_deafen"))
        self.dc_leave_btn = QPushButton(tr("discord_leave"))
        for b, act_id, col in (
            (self.dc_mute_btn, "mute", "#E2E8F0"),
            (self.dc_deaf_btn, "deafen", "#E2E8F0"),
            (self.dc_leave_btn, "leave", "#FCA5A5"),
        ):
            b.setFixedHeight(26)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.setStyleSheet(
                f"QPushButton {{ color: {col}; border-radius: 9px; font-size: 10px; font-weight: 600; padding: 0 6px; }}"
            )
            b.clicked.connect(lambda _, a=act_id: self.discord_action.emit(a))
            dc_btns.addWidget(b, 1)
        dc_lay.addLayout(dc_btns, 1)
        root.addWidget(discord_card)

        dnd_card = QFrame()
        dnd_card.setObjectName("Card")
        dnd_card.setMinimumWidth(0)
        dnd_card.setStyleSheet("QFrame#Card { border-radius: 12px; }")
        dn_lay = QHBoxLayout(dnd_card)
        dn_lay.setContentsMargins(10, 6, 10, 6)
        dn_lay.setSpacing(6)
        dn_hdr = QVBoxLayout()
        dn_hdr.setSpacing(0)
        dn_title = QLabel(tr("dnd_title"))
        dn_title.setStyleSheet("color: #A78BFA; font-size: 10px; font-weight: 800; background: transparent;")
        self.dnd_status_lbl = QLabel("Выкл")
        self.dnd_status_lbl.setStyleSheet("color: #94A3B8; font-size: 9px; background: transparent;")
        dn_hdr.addWidget(dn_title)
        dn_hdr.addWidget(self.dnd_status_lbl)
        dn_hdr_w = QWidget()
        dn_hdr_w.setFixedWidth(96)
        dn_hdr_w.setStyleSheet("background: transparent;")
        dn_hdr_w.setLayout(dn_hdr)
        dn_lay.addWidget(dn_hdr_w)

        dn_btns = QHBoxLayout()
        dn_btns.setSpacing(5)
        self.dnd_btns: dict[int, QPushButton] = {}
        for mins, lbl in ((30, "30 мин"), (60, "1 ч"), (120, "2 ч"), (0, "Выкл")):
            b = QPushButton(lbl)
            b.setFixedHeight(26)
            b.setMinimumWidth(0)
            b.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            b.setStyleSheet(
                "QPushButton { border-radius: 9px; font-size: 10px; font-weight: 600; padding: 0 6px; }"
            )
            b.clicked.connect(lambda _, m=mins: self.dnd_requested.emit(m))
            self.dnd_btns[mins] = b
            dn_btns.addWidget(b, 1)
        dn_lay.addLayout(dn_btns, 1)
        root.addWidget(dnd_card)

    def _on_power_clicked(self, key: str) -> None:
        if key == "lock":
            self._disarm_power()
            self.power_requested.emit("lock")
            return
        if self._power_armed == key:
            self._disarm_power()
            self.power_requested.emit(key)
            return
        self._disarm_power()
        self._power_armed = key
        btn = self.power_btns.get(key)
        if btn:
            btn.setText("Точно?")
            btn.setStyleSheet(
                "QPushButton { background: rgba(220, 38, 38, 195); color: #FFFFFF; border: 1px solid rgba(255,140,140,0.45); border-radius: 10px; font-size: 11px; font-weight: 700; padding: 0 6px; }"
            )
        self._power_reset_timer.start(3500)

    def _disarm_power(self) -> None:
        self._power_reset_timer.stop()
        if not self._power_armed:
            return
        old = self._power_armed
        self._power_armed = ""
        btn = self.power_btns.get(old)
        if btn:
            btn.setText(self._power_labels.get(old, old))
            btn.setStyleSheet("QPushButton { border-radius: 10px; font-size: 11px; padding: 0 6px; }")

    def update_system_state(
        self,
        snap: object,
        *,
        wifi_on: bool | None = None,
        bt_on: bool | None = None,
        airplane_on: bool | None = None,
        vpn_on: bool | None = None,
        desktop_clean: bool | None = None,
        night_light_on: bool | None = None,
    ) -> None:
        if snap is None:
            return
        cpu_pct = float(getattr(snap, "cpu_percent", 0.0) or 0.0)
        cpu_freq = float(getattr(snap, "cpu_freq_mhz", 0.0) or 0.0)
        gpu_pct = float(getattr(snap, "gpu_load", -1.0) or 0.0)
        gpu_name = str(getattr(snap, "gpu_name", "") or "GPU").strip()
        gpu_temp = float(getattr(snap, "gpu_temp", -1.0) or -1.0)
        ram_pct = float(getattr(snap, "ram_percent", 0.0) or 0.0)
        ram_used = float(getattr(snap, "ram_used_gb", 0.0) or 0.0)
        ram_tot = float(getattr(snap, "ram_total_gb", 0.0) or 0.0)

        cpu_temp = float(getattr(snap, "cpu_temp", -1.0) or -1.0)
        ram_temp = float(getattr(snap, "ram_temp", -1.0) or -1.0)
        if cpu_freq > 100.0:
            cpu_sub = f"{cpu_freq / 1000.0:.1f} ГГц • {cpu_temp:.0f}°C" if cpu_temp > 0.0 else f"{cpu_freq / 1000.0:.1f} ГГц • ЦП"
        else:
            cpu_sub = f"ЦП • {cpu_temp:.0f}°C" if cpu_temp > 0.0 else f"Загрузка ЦП • {cpu_pct:.1f}%"
        self.cpu_card.set_metrics(cpu_pct, f"{cpu_pct:.0f}%", cpu_sub, title_text=tr("cpu_title").upper())
        clean_gpu = gpu_name
        for prefix in ("NVIDIA GeForce ", "AMD Radeon ", "Intel(R) ", "Intel "):
            if clean_gpu.startswith(prefix):
                clean_gpu = clean_gpu[len(prefix):]
                break
        short_gpu = (clean_gpu[:15] + "…") if len(clean_gpu) > 16 else clean_gpu
        self.gpu_card.setToolTip(gpu_name)
        if gpu_pct >= 0.0:
            gpu_sub = f"{short_gpu} • {gpu_temp:.0f}°C" if gpu_temp > 0.0 else (short_gpu or "Видеоадаптер")
            self.gpu_card.set_metrics(gpu_pct, f"{gpu_pct:.0f}%", gpu_sub, title_text=tr("gpu_title").upper())
        else:
            self.gpu_card.set_metrics(0.0, "—", short_gpu or "Видеоадаптер", title_text=tr("gpu_title").upper())
        ram_sub = f"Занято {ram_pct:.0f}% • {ram_temp:.0f}°C" if ram_temp > 0.0 else f"Занято {ram_pct:.0f}%"
        self.ram_card.set_metrics(ram_pct, f"{ram_used:.1f}/{ram_tot:.0f} ГБ", ram_sub, title_text=tr("memory_title").upper())

        # Laptop-only battery indicator inside Control Center header
        b_pct = getattr(snap, "battery_percent", -1)
        b_chg = bool(getattr(snap, "battery_plugged", False))
        if b_pct is not None and int(b_pct) >= 0:
            self.batt_badge.setText(f"{'⚡ ' if b_chg else ''}{int(b_pct)}%")
            self.batt_badge.show()
        else:
            self.batt_badge.hide()

        wifi_ssid = str(getattr(snap, "wifi_ssid", "") or "").strip()
        net_if = str(getattr(snap, "net_interface", "") or "").strip()
        snap_wifi = getattr(snap, "wifi_radio_on", None)
        if wifi_on is not None:
            w_active = bool(wifi_on)
        elif wifi_ssid:
            w_active = True
        elif snap_wifi is not None:
            w_active = bool(snap_wifi)
        else:
            w_active = False

        bt_devs = getattr(snap, "bluetooth_devices", None) or []
        snap_bt = getattr(snap, "bt_radio_on", None)
        if bt_on is not None:
            b_active = bool(bt_on)
        elif snap_bt is not None:
            b_active = bool(snap_bt)
        else:
            b_active = False

        a_active = bool(getattr(snap, "airplane_on", False)) if airplane_on is None else bool(airplane_on)
        v_active = bool(getattr(snap, "vpn_active", False)) if vpn_on is None else bool(vpn_on)
        vpn_name = str(getattr(snap, "vpn_name", "") or "").strip()
        d_active = bool(getattr(snap, "desktop_clean", False)) if desktop_clean is None else bool(desktop_clean)

        w_sub = wifi_ssid[:9] if (w_active and wifi_ssid) else ("Вкл" if w_active else ("LAN" if net_if == "Ethernet" else "Выкл"))
        self._last_wifi_ssid = wifi_ssid
        self._last_bt_devs = list(bt_devs)
        self._last_vpn_active = v_active
        self._last_vpn_name = vpn_name
        self.radio_toggles["wifi"].set_state(w_active, w_sub)
        self._last_bt_active = bool(b_active)
        bt_sub = self._format_bt_subtitle(b_active, bt_devs)
        self.radio_toggles["bt"].set_state(b_active, bt_sub)
        self.radio_toggles["airplane"].set_state(a_active, "Вкл" if a_active else "Выкл")
        self.radio_toggles["vpn"].set_state(v_active, (vpn_name[:9] if (v_active and vpn_name) else ("Подкл." if v_active else "Amnezia")))
        self.radio_toggles["desktop"].set_state(d_active, "Скрыто" if d_active else "Обычно")
        if night_light_on is None:
            now_nl = time.monotonic()
            if (now_nl - getattr(self, "_nl_poll_ts", 0.0)) >= 3.0:
                from ...core.win32_api import is_windows_night_light_enabled
                self._nl_poll_ts = now_nl
                self._nl_cached_val = bool(is_windows_night_light_enabled())
            nl_active = bool(getattr(self, "_nl_cached_val", False))
        else:
            nl_active = bool(night_light_on)
            self._nl_cached_val = nl_active
            self._nl_poll_ts = time.monotonic()
        self.radio_toggles["night_light"].set_state(nl_active, "Вкл" if nl_active else "Выкл")

    def update_discord_voice(self, in_voice: bool, muted: bool, deafened: bool, *, running: bool = True) -> None:
        sig = (bool(running), bool(in_voice), bool(muted), bool(deafened))
        if getattr(self, "_last_dc_voice_sig", None) == sig:
            return
        self._last_dc_voice_sig = sig
        self.discord_card.setVisible(bool(running))
        if not running:
            self.discord_status_lbl.setText("Не запущен")
        elif in_voice:
            self.discord_status_lbl.setText("В канале")
        else:
            self.discord_status_lbl.setText("Готово")
        self.dc_mute_btn.setText(tr("discord_mute_off") if muted else tr("discord_mute"))
        self.dc_mute_btn.setStyleSheet(
            "QPushButton { background: rgba(239, 68, 68, 130); color: #F8FAFC; border-radius: 9px; font-size: 10px; font-weight: 600; padding: 0 5px; }"
            if muted
            else "QPushButton { color: #E2E8F0; border-radius: 9px; font-size: 10px; font-weight: 600; padding: 0 5px; }"
        )
        self.dc_deaf_btn.setText(tr("discord_deafen_off") if deafened else tr("discord_deafen"))
        self.dc_deaf_btn.setStyleSheet(
            "QPushButton { background: rgba(239, 68, 68, 130); color: #F8FAFC; border-radius: 9px; font-size: 10px; font-weight: 600; padding: 0 5px; }"
            if deafened
            else "QPushButton { color: #E2E8F0; border-radius: 9px; font-size: 10px; font-weight: 600; padding: 0 5px; }"
        )
        self.dc_leave_btn.setText(tr("discord_leave"))

    def update_dnd_status(self, until_ts: float) -> None:
        now = time.time()
        if until_ts > now + 2.0:
            rem_min = max(1, int(round((until_ts - now) / 60.0)))
            dt_str = time.strftime("%H:%M", time.localtime(until_ts))
            txt = f"До {dt_str} ({rem_min}м)"
            qss = "color: #C4B5FD; font-size: 9px; font-weight: 700; background: transparent;"
        else:
            txt = "Выкл"
            qss = "color: #94A3B8; font-size: 9px; background: transparent;"
        if self.dnd_status_lbl.text() != txt:
            self.dnd_status_lbl.setText(txt)
        if getattr(self.dnd_status_lbl, "_cached_qss", None) != qss:
            self.dnd_status_lbl._cached_qss = qss
            self.dnd_status_lbl.setStyleSheet(qss)

    def _toggle_inline_menu(self, key: str) -> None:
        if not key or key == self._open_menu_key:
            self._open_menu_key = ""
            self.inline_subpanel.hide()
            return
        self._open_menu_key = key
        self.wifi_auth_row.hide()
        self.sub_status_lbl.setText("")
        titles = {
            "wifi": "Сети Wi-Fi",
            "bt": "Устройства Bluetooth",
            "vpn": "VPN / Amnezia",
            "desktop": "Чистый рабочий стол",
            "night_light": "Ночной свет (Синий фильтр)",
        }
        self.sub_title_lbl.setText(titles.get(key, key.upper()))
        self.sub_refresh_btn.setVisible(key in ("wifi", "bt"))
        self.sub_list.setVisible(key in ("wifi", "bt"))
        self.inline_subpanel.show()
        self._refresh_inline_menu()

    def _open_subpanel_os_settings(self) -> None:
        from ...core.win32_api import open_windows_uri

        uri_map = {
            "wifi": "ms-settings:network-wifi",
            "bt": "ms-settings:bluetooth",
            "vpn": "ms-settings:network-vpn",
            "night_light": "ms-settings:nightlight",
        }
        uri = uri_map.get(self._open_menu_key, "ms-settings:network")
        open_windows_uri(uri)

    def _refresh_inline_menu(self) -> None:
        k = self._open_menu_key
        if not k:
            return
        if k == "wifi":
            self.sub_list.clear()
            self.sub_list.addItem("Поиск сетей Wi-Fi…")
            def _scan() -> None:
                nets: list[tuple[str, str]] = []
                try:
                    out = subprocess.check_output(
                        ["netsh", "wlan", "show", "networks", "mode=bssid"],
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                        timeout=4,
                        creationflags=0x08000000,
                    )
                    cur_ssid = ""
                    cur_sig = ""
                    for raw in out.splitlines():
                        line = raw.strip()
                        if line.startswith("SSID ") and ":" in line:
                            if cur_ssid:
                                nets.append((cur_ssid, cur_sig))
                            cur_ssid = line.split(":", 1)[1].strip()
                            cur_sig = ""
                        elif ("Сигнал" in line or "Signal" in line) and ":" in line and not cur_sig:
                            cur_sig = line.split(":", 1)[1].strip()
                    if cur_ssid:
                        nets.append((cur_ssid, cur_sig))
                except Exception:
                    pass
                try:
                    self.wifi_scan_ready.emit(nets[:16])
                except RuntimeError:
                    pass

            threading.Thread(target=_scan, daemon=True).start()
        elif k == "bt":
            self._populate_bt_sub_list(self._last_bt_devs)
            self.sub_status_lbl.setText(
                "Обновление статуса Bluetooth…" if self._bt_scan_busy else "Нажмите «Параметры» для сопряжения или подключения"
            )
            self._trigger_bt_refresh()
        elif k == "vpn":
            st = f"Активен: {self._last_vpn_name or 'VPN'}" if self._last_vpn_active else "Отключён (Amnezia / системный VPN)"
            self.sub_status_lbl.setText(st)
        elif k == "desktop":
            self.sub_status_lbl.setText("Нажмите на плитку «Чистый стол» или настройте здесь для скрытия значков.")
        elif k == "night_light":
            self.sub_status_lbl.setText("Тёплый фильтр экрана Windows. Нажмите «Параметры» для графика.")

    def _on_wifi_scan_ready(self, nets: list) -> None:
        if self._open_menu_key != "wifi":
            return
        self.sub_list.clear()
        if not nets:
            self.sub_list.addItem("Сети Wi-Fi не найдены или адаптер выключен")
            return
        seen: set[str] = set()
        for ssid, sig in nets:
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            mark = "✓ " if ssid == self._last_wifi_ssid else ""
            it = QListWidgetItem(f"{mark}{ssid}" + (f"  ({sig})" if sig else ""))
            it.setData(Qt.ItemDataRole.UserRole, ssid)
            self.sub_list.addItem(it)
        self.sub_status_lbl.setText("Нажмите сеть из списка для ввода пароля и подключения")

    @staticmethod
    def _format_bt_subtitle(b_active: bool, bt_devs: list) -> str:
        if not b_active:
            return "Выкл"
        conn_cnt = sum(1 for d in bt_devs if getattr(d, "connected", False))
        if conn_cnt > 0:
            return f"{conn_cnt} подкл."
        if bt_devs:
            return f"{len(bt_devs)} сопр."
        return "Вкл"

    def _populate_bt_sub_list(self, devs: list) -> None:
        self.sub_list.clear()
        if not devs:
            self.sub_list.addItem("Нет сопряжённых Bluetooth-устройств")
            return
        ordered = sorted(
            devs,
            key=lambda b: (not bool(getattr(b, "connected", False)), str(getattr(b, "name", b)).casefold()),
        )
        for b in ordered:
            name = str(getattr(b, "name", b)).strip()
            conn = bool(getattr(b, "connected", False))
            bat = getattr(b, "battery", None) or getattr(b, "battery_percent", None)
            suffix = " • Подключено" if conn else " • Не в сети"
            if bat is not None and int(bat) >= 0:
                suffix += f" • 🔋{int(bat)}%"
            it = QListWidgetItem(f"{name}{suffix}")
            it.setForeground(QColor("#4ADE80" if conn else "#94A3B8"))
            self.sub_list.addItem(it)

    def _trigger_bt_refresh(self) -> None:
        if self._bt_scan_busy:
            return
        self._bt_scan_busy = True

        def _scan() -> None:
            devs: list = []
            try:
                from ...services.system_service import SystemMonitorService

                devs = SystemMonitorService._scan_bluetooth_devices()
            except Exception:
                devs = list(self._last_bt_devs)
            self.bt_scan_ready.emit(devs)

        threading.Thread(target=_scan, daemon=True).start()

    def _on_bt_scan_ready(self, devs: list) -> None:
        self._bt_scan_busy = False
        self._last_bt_devs = list(devs)
        self.radio_toggles["bt"].set_state(
            self._last_bt_active,
            self._format_bt_subtitle(self._last_bt_active, devs),
        )
        if self._open_menu_key != "bt":
            return
        self._populate_bt_sub_list(devs)
        self.sub_status_lbl.setText("Нажмите «Параметры» для сопряжения или подключения")

    def _on_sub_list_clicked(self, item: QListWidgetItem) -> None:
        if self._open_menu_key != "wifi":
            return
        ssid = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not ssid:
            return
        self._wifi_selected_ssid = ssid
        self.wifi_pwd_edit.setPlaceholderText(f"Пароль для «{ssid}» (пусто если сохранён)…")
        self.wifi_auth_row.show()

    def _connect_selected_wifi(self) -> None:
        ssid = self._wifi_selected_ssid.strip()
        if not ssid:
            return
        pwd = self.wifi_pwd_edit.text()
        self.sub_status_lbl.setText(f"Подключение к {ssid}…")
        self.wifi_auth_row.hide()

        def _conn() -> None:
            try:
                if pwd:
                    import html
                    import tempfile

                    safe_s = html.escape(ssid)
                    safe_p = html.escape(pwd)
                    xml = (
                        '<?xml version="1.0"?>'
                        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
                        f"<name>{safe_s}</name><SSIDConfig><SSID><name>{safe_s}</name></SSID></SSIDConfig>"
                        "<connectionType>ESS</connectionType><connectionMode>auto</connectionMode>"
                        "<MSM><security><authEncryption><authentication>WPA2PSK</authentication>"
                        "<encryption>AES</encryption><useOneX>false</useOneX></authEncryption>"
                        f"<sharedKey><keyType>passPhrase</keyType><protected>false</protected><keyMaterial>{safe_p}</keyMaterial></sharedKey>"
                        "</security></MSM></WLANProfile>"
                    )
                    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as tf:
                        tf.write(xml)
                        tf_path = tf.name
                    try:
                        subprocess.run(["netsh", "wlan", "add", "profile", f"filename={tf_path}"], creationflags=0x08000000, timeout=4)
                    finally:
                        Path(tf_path).unlink(missing_ok=True)
                subprocess.run(["netsh", "wlan", "connect", f"name={ssid}"], creationflags=0x08000000, timeout=4)
            except Exception:
                pass

        threading.Thread(target=_conn, daemon=True).start()

    def _style_subpanel_buttons(self, *, is_dark: bool, fg: str, accent: str) -> None:
        """Apply circular pill styles and theme-matched vector icons to inline subpanel header buttons."""
        pill_bg = "rgba(255, 255, 255, 18)" if is_dark else "rgba(15, 23, 42, 14)"
        pill_bd = "rgba(255, 255, 255, 42)" if is_dark else "rgba(15, 23, 42, 34)"
        pill_hv = "rgba(255, 255, 255, 34)" if is_dark else "rgba(15, 23, 42, 26)"
        self.sub_refresh_btn.setIcon(get_icon("refresh", size=14, color=fg, accent=accent))
        self.sub_refresh_btn.setStyleSheet(
            f"QPushButton {{ min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; "
            f"border-radius: 13px; padding: 0px; background-color: {pill_bg}; border: 1px solid {pill_bd}; }}"
            f"QPushButton:hover {{ background-color: rgba(96, 165, 250, 48); border-color: {accent}; }}"
        )
        self.sub_os_btn.setStyleSheet(
            f"QPushButton {{ min-height: 26px; max-height: 26px; font-size: 10px; font-weight: 600; "
            f"padding: 0px 10px; border-radius: 13px; background-color: {pill_bg}; border: 1px solid {pill_bd}; }}"
            f"QPushButton:hover {{ background-color: {pill_hv}; border-color: {accent}; }}"
        )
        self.sub_close_btn.setIcon(get_icon("close", size=12, color=fg, accent="#F87171"))
        self.sub_close_btn.setStyleSheet(
            f"QPushButton {{ min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; "
            f"border-radius: 13px; padding: 0px; background-color: {pill_bg}; border: 1px solid {pill_bd}; }}"
            "QPushButton:hover { background-color: rgba(248, 113, 113, 52); border-color: #F87171; }"
        )

    def apply_palette(self, pal: ThemePalette) -> None:
        self._accent_hex = pal.accent
        self._icon_fg = pal.text_primary
        self._style_subpanel_buttons(is_dark=pal.is_dark, fg=pal.text_primary, accent=pal.accent)
        for key, btn in self.power_btns.items():
            if self._power_armed != key:
                btn.setIcon(get_icon(self._power_icons[key], size=13, color=pal.text_primary, accent="#F87171" if key == "shutdown" else pal.accent))


class HomeTab(QWidget):
    """Main tab combining GSMTC player, Atoll Control Center, quick tools, and custom launcher."""

    tool_triggered = pyqtSignal(str)  # tool_id
    volume_polled = pyqtSignal(int, bool, bool)  # (volume_pct, muted, per_app_found)
    favicon_ready = pyqtSignal()
    control_toggled = pyqtSignal(str)
    power_requested = pyqtSignal(str)
    discord_action = pyqtSignal(str)
    dnd_requested = pyqtSignal(int)

    def __init__(
        self,
        media_service: MediaService,
        storage: LocalStorage,
        parent: QWidget | None = None,
        *,
        config_mgr: ConfigManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.media_service = media_service
        self.storage = storage
        self.config_mgr = config_mgr
        self._favicon_dir = self.storage.db_path.parent / "favicons"
        self._favicon_loading: set[str] = set()
        self._icon_provider = QFileIconProvider()
        self._duration = 0.0
        self._seeking = False
        self._vol_dragging = False
        self._last_vol_send = 0.0
        self._show_lyrics = False
        self._active_group = ""
        self._last_snapshot = MediaStateSnapshot()
        self._accent_hex = resolve_time_accent()
        self._icon_fg = "#F2F5FA"
        self._muted = False
        self._player_hint = "spotify"
        self._show_dev_tools = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        scroll.setWidget(body)
        cols_layout = QHBoxLayout(body)
        cols_layout.setContentsMargins(4, 0, 4, 4)
        cols_layout.setSpacing(6)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(4)
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(4)
        cols_layout.addLayout(left_col, 11)
        cols_layout.addLayout(right_col, 10)

        # --- Media Card (auto-hidden when inactive & Spotify closed) ---
        self.media_card = QFrame()
        self.media_card.setObjectName("Card")
        self.media_card.setMinimumWidth(0)
        mc_lay = QVBoxLayout(self.media_card)
        mc_lay.setContentsMargins(8, 5, 8, 5)
        mc_lay.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self._last_art_qimg: QImage | None = None
        self.art_lbl = QLabel("")
        self.art_lbl.setFixedSize(82, 82)
        self.art_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_lbl.setPixmap(get_pixmap("music", size=34, color="#F2F5FA", accent=self._accent_hex))
        self.art_lbl.setStyleSheet("background: rgba(255,255,255,0.06); border-radius: 18px;")
        top_row.addWidget(self.art_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self._raw_media_title = tr("no_music")
        self._raw_media_artist = ""
        self.title_lbl = QLabel(tr("no_music"))
        self.title_lbl.setObjectName("Heading")
        self.title_lbl.setMinimumWidth(0)
        self.title_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.source_btn = QPushButton("Источник")
        self.source_btn.setIcon(get_icon("spotify", size=14, color="#F2F5FA", accent="#1DB954"))
        self.source_btn.setIconSize(QSize(14, 14))
        self.source_btn.setToolTip("Открыть активный плеер (включая Spotify)")
        self.source_btn.setFixedHeight(24)
        self.source_btn.setStyleSheet(
            "min-height: 22px; max-height: 24px; padding: 0px 9px; border-radius: 12px; font-size: 11px;"
        )
        self.source_btn.clicked.connect(lambda: self.media_service.open_source_app())
        self.source_btn.hide()
        title_row.addWidget(self.title_lbl, 1)
        title_row.addWidget(self.source_btn)

        self.artist_lbl = QLabel("")
        self.artist_lbl.setObjectName("Muted")
        self.artist_lbl.setMinimumWidth(0)
        self.artist_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        meta_col.addLayout(title_row)
        meta_col.addWidget(self.artist_lbl)

        sel_row = QHBoxLayout()
        self.player_combo = QComboBox()
        self.player_combo.setMinimumWidth(0)
        self.player_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.player_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.player_combo.setFixedHeight(28)
        self.player_combo.addItem("Auto Player", "")
        self.player_combo.currentIndexChanged.connect(self._on_player_selected)
        self.lyrics_btn = QPushButton(tr("lyrics"))
        self.lyrics_btn.setMinimumWidth(0)
        self.lyrics_btn.setFixedHeight(28)
        self.lyrics_btn.setStyleSheet(
            "min-height: 26px; max-height: 28px; border-radius: 14px; padding: 0px 10px; font-size: 11px;"
        )
        self.lyrics_btn.setCheckable(True)
        self.lyrics_btn.clicked.connect(self._toggle_lyrics)
        sel_row.addWidget(self.player_combo, 1)
        sel_row.addWidget(self.lyrics_btn)
        meta_col.addLayout(sel_row)
        top_row.addLayout(meta_col, 1)
        mc_lay.addLayout(top_row)

        self.spectrum = SpectrumBarWidget()
        self.spectrum.setFixedHeight(16)
        mc_lay.addWidget(self.spectrum)

        seek_row = QHBoxLayout()
        self.pos_lbl = QLabel("0:00")
        self.pos_lbl.setObjectName("Muted")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.dur_lbl = QLabel("0:00")
        self.dur_lbl.setObjectName("Muted")
        seek_row.addWidget(self.pos_lbl)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.dur_lbl)
        mc_lay.addLayout(seek_row)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)
        self.prev_btn = QPushButton("")
        self.prev_btn.setFixedSize(32, 32)
        self.prev_btn.setStyleSheet(
            "min-width: 30px; max-width: 32px; min-height: 30px; max-height: 32px; "
            "border-radius: 16px; padding: 0px;"
        )
        self.prev_btn.setIcon(get_icon("prev", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.prev_btn.setIconSize(QSize(15, 15))
        self.play_btn = QPushButton(" Играть" if (self.config_mgr and self.config_mgr.config.language == "ru") else " Play")
        self.play_btn.setMinimumWidth(0)
        self.play_btn.setFixedHeight(32)
        self.play_btn.setStyleSheet(
            f"QPushButton {{ min-height: 30px; max-height: 32px; border-radius: 16px; padding: 0px 14px; "
            f"font-weight: 700; background-color: {self._accent_hex}; color: #0B0D13; border: 1px solid {self._accent_hex}; }}"
        )
        self.play_btn.setIcon(get_icon("play", size=14, color="#0B0D13", accent="#0B0D13"))
        self.play_btn.setIconSize(QSize(14, 14))
        self.play_btn.setObjectName("Primary")
        self.next_btn = QPushButton("")
        self.next_btn.setFixedSize(32, 32)
        self.next_btn.setStyleSheet(
            "min-width: 30px; max-width: 32px; min-height: 30px; max-height: 32px; "
            "border-radius: 16px; padding: 0px;"
        )
        self.next_btn.setIcon(get_icon("next", size=15, color=self._icon_fg, accent=self._accent_hex))
        self.next_btn.setIconSize(QSize(15, 15))
        self.prev_btn.clicked.connect(self.media_service.prev_track)
        self.play_btn.clicked.connect(self.media_service.play_pause)
        self.next_btn.clicked.connect(self.media_service.next_track)
        ctrl_row.addWidget(self.prev_btn)
        ctrl_row.addWidget(self.play_btn, 1)
        ctrl_row.addWidget(self.next_btn)
        mc_lay.addLayout(ctrl_row)

        # --- Per-Player / Spotify Volume Mixer Row ---
        vol_row = QHBoxLayout()
        vol_row.setSpacing(6)
        self.vol_btn = QPushButton("")
        self.vol_btn.setIcon(get_icon("volume", size=14, color=self._icon_fg, accent=self._accent_hex))
        self.vol_btn.setIconSize(QSize(14, 14))
        self.vol_btn.setFixedSize(24, 24)
        self.vol_btn.setStyleSheet(
            "min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; "
            "border-radius: 12px; padding: 0px;"
        )
        self.vol_btn.setToolTip("Громкость плеера / Spotify")
        self.vol_btn.clicked.connect(self._toggle_player_mute)
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(85)
        self.vol_slider.sliderPressed.connect(lambda: setattr(self, "_vol_dragging", True))
        self.vol_slider.sliderReleased.connect(self._on_vol_released)
        self.vol_slider.valueChanged.connect(self._on_vol_changed)
        self.vol_lbl = QLabel("85%")
        self.vol_lbl.setObjectName("Muted")
        self.vol_lbl.setMinimumWidth(52)
        self.vol_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        vol_row.addWidget(self.vol_btn)
        vol_row.addWidget(self.vol_slider, 1)
        vol_row.addWidget(self.vol_lbl)
        mc_lay.addLayout(vol_row)
        self.volume_polled.connect(self._apply_polled_volume)
        QTimer.singleShot(250, self._refresh_player_volume)

        self.active_lyric_btn = QPushButton("")
        self.active_lyric_btn.setFlat(True)
        self.active_lyric_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.active_lyric_btn.setFixedHeight(22)
        self.active_lyric_btn.setStyleSheet(
            "QPushButton { background: rgba(255, 255, 255, 16); color: #F1F5F9; "
            "border: 1px solid rgba(255, 255, 255, 28); border-radius: 11px; "
            "padding: 0px 10px; font-size: 11px; font-weight: 600; text-align: left; } "
            "QPushButton:hover { background: rgba(255, 255, 255, 28); }"
        )
        self.active_lyric_btn.clicked.connect(lambda: self.lyrics_btn.toggle())
        self.active_lyric_btn.hide()
        mc_lay.addWidget(self.active_lyric_btn)

        self.lyrics_list = QListWidget()
        self.lyrics_list.setMaximumHeight(140)
        self.lyrics_list.hide()
        self.lyrics_list.itemClicked.connect(self._on_lyric_clicked)
        mc_lay.addWidget(self.lyrics_list)

        self.media_card.setVisible(False)
        left_col.addWidget(self.media_card)

        # --- Atoll Unified Control Hub (5 Radios + Power + CPU/GPU/Memory + Discord + DND) ---
        self.control_hub = _AtollControlHub(self)
        self.control_hub.control_toggled.connect(self.control_toggled.emit)
        self.control_hub.power_requested.connect(self.power_requested.emit)
        self.control_hub.discord_action.connect(self.discord_action.emit)
        self.control_hub.dnd_requested.connect(self.dnd_requested.emit)
        if self.config_mgr is not None:
            self.control_hub.update_dnd_status(getattr(self.config_mgr.config, "dnd_until_ts", 0.0))
        right_col.addWidget(self.control_hub)
        right_col.addStretch(1)

        # --- Quick Tools Card ---
        self.tools_card = QFrame()
        self.tools_card.setObjectName("Card")
        tc_lay = QVBoxLayout(self.tools_card)
        tc_lay.setContentsMargins(8, 5, 8, 5)
        tc_lay.setSpacing(3)
        tc_hdr = QHBoxLayout()
        tc_hdr.setSpacing(6)
        self.tools_hdr_lbl = QLabel(tr("quick_actions"))
        self.tools_hdr_lbl.setObjectName("Heading")
        self.rec_audio_combo = QComboBox()
        self.rec_audio_combo.setToolTip("Звук при записи экрана")
        self.rec_audio_combo.setFixedHeight(24)
        self.rec_audio_combo.addItem("🔇 Без звука", "none")
        self.rec_audio_combo.addItem("🔊 Звук ПК", "system")
        self.rec_audio_combo.addItem("🎙 ПК + Микрофон", "both")
        cur_ram = str(getattr(self.config_mgr.config, "recording_audio_mode", "system") if self.config_mgr else "system").lower()
        self.rec_audio_combo.setCurrentIndex(max(0, self.rec_audio_combo.findData(cur_ram)))
        self.rec_audio_combo.currentIndexChanged.connect(self._on_rec_audio_changed)
        tc_hdr.addWidget(self.tools_hdr_lbl, 1)
        tc_hdr.addWidget(self.rec_audio_combo)
        tc_lay.addLayout(tc_hdr)

        self._tools_grid = QGridLayout()
        self._tools_grid.setSpacing(4)
        self.tool_buttons: dict[str, QPushButton] = {}
        self._tool_icon_map: dict[str, str] = {
            "screenshot_area": "screenshot",
            "screenshot_full": "fullscreen",
            "ocr": "ocr",
            "color_picker": "color",
            "restart_screens": "monitor_reset",
            "qr_scan": "qr",
            "record": "record",
            "dictate": "dictate",
            "command_bar": "command",
        }
        tools = [
            ("screenshot_area", tr("screenshot")),
            ("screenshot_full", tr("screenshot_full")),
            ("ocr", tr("ocr")),
            ("color_picker", tr("color_picker")),
            ("restart_screens", tr("restart_screens")),
            ("qr_scan", tr("qr_scan")),
            ("record", tr("record")),
            ("dictate", tr("dictate")),
            ("command_bar", tr("command_bar")),
        ]
        self._tool_order = [tid for tid, _ in tools]
        for tid, label in tools:
            btn = QPushButton(label)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.setFixedHeight(23)
            btn.setStyleSheet(
                "QPushButton { min-height: 23px; max-height: 23px; padding: 0px 6px; font-size: 10px; border-radius: 10px; }"
            )
            btn.setToolTip(label)
            btn.setIcon(get_icon(self._tool_icon_map[tid], size=13, color=self._icon_fg, accent=self._accent_hex))
            btn.setIconSize(QSize(13, 13))
            btn.clicked.connect(lambda _, t=tid: self.tool_triggered.emit(t))
            self.tool_buttons[tid] = btn
        tc_lay.addLayout(self._tools_grid)
        self._relayout_tools()
        left_col.addWidget(self.tools_card)

        # --- Launcher Card (Atoll-style Horizontal Circular App Dock) ---
        self.launch_card = QFrame()
        self.launch_card.setObjectName("Card")
        lc_lay = QVBoxLayout(self.launch_card)
        lc_lay.setContentsMargins(8, 5, 8, 5)
        lc_lay.setSpacing(2)

        lh = QHBoxLayout()
        lh_lbl = QLabel(tr("launcher"))
        lh_lbl.setObjectName("Heading")
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._on_group_changed)
        lh.addWidget(lh_lbl)
        lh.addWidget(self.group_combo, 1)
        lh.addStretch(1)
        lc_lay.addLayout(lh)

        self.launcher_scroll = QScrollArea()
        self.launcher_scroll.setWidgetResizable(True)
        self.launcher_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.launcher_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.launcher_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.launcher_scroll.setFixedHeight(82)
        self.launcher_scroll.setStyleSheet("background: transparent;")

        self.launcher_grid_host = QWidget()
        self.launcher_grid_host.setStyleSheet("background: transparent;")
        self.launcher_grid = QGridLayout(self.launcher_grid_host)
        self.launcher_grid.setContentsMargins(4, 3, 4, 3)
        self.launcher_grid.setHorizontalSpacing(2)
        self.launcher_grid.setVerticalSpacing(4)
        self.launcher_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._launcher_bubbles: list[_AtollAppBubble] = []
        self._launcher_cols = 0
        self.launcher_scroll.setWidget(self.launcher_grid_host)
        lc_lay.addWidget(self.launcher_scroll)
        left_col.addWidget(self.launch_card)
        left_col.addStretch(1)

        self.add_btn = _AtollAppBubble("Добавить приложение или сайт", accent_hex=self._accent_hex, is_add=True)
        self.add_btn.clicked.connect(self._add_shortcut)

        self.favicon_ready.connect(self.refresh_launcher)
        self.refresh_launcher()
        self.apply_home_layout()

    def update_system_state(self, snap: object, **kwargs: bool | None) -> None:
        self.control_hub.update_system_state(snap, **kwargs)

    def update_discord_voice(self, in_voice: bool, muted: bool, deafened: bool, *, running: bool = True) -> None:
        self.control_hub.update_discord_voice(in_voice, muted, deafened, running=running)

    def update_dnd_status(self, until_ts: float) -> None:
        self.control_hub.update_dnd_status(until_ts)

    def _on_rec_audio_changed(self) -> None:
        if self.config_mgr is None:
            return
        mode = str(self.rec_audio_combo.currentData() or "system")
        self.config_mgr.config.recording_audio_mode = mode
        self.config_mgr.save()

    def apply_home_layout(self) -> None:
        """Apply player_card_style (mirror | fill | compact), home_hidden_blocks & home_hidden_tools."""
        cfg = self.config_mgr.config if self.config_mgr is not None else None
        hidden_blocks = set(getattr(cfg, "home_hidden_blocks", []) or []) if cfg else set()
        if hasattr(self, "media_card"):
            if "media" in hidden_blocks:
                self.media_card.setVisible(False)
            else:
                snap = getattr(self, "_last_snapshot", None)
                if snap is not None:
                    has_spotify = any(
                        "spotify" in ((s.source_id or "") + " " + (s.app_id or "")).lower()
                        for s in (snap.sessions or [])
                    )
                    self.media_card.setVisible(bool(snap.active is not None or has_spotify))
        if hasattr(self, "control_hub"):
            self.control_hub.setVisible("control_hub" not in hidden_blocks)
        if hasattr(self, "tools_card"):
            self.tools_card.setVisible("tools" not in hidden_blocks)
        if hasattr(self, "launch_card"):
            self.launch_card.setVisible("launcher" not in hidden_blocks)
        if cfg and hasattr(self, "rec_audio_combo"):
            cur_ram = str(getattr(cfg, "recording_audio_mode", "system") or "system").lower()
            idx = self.rec_audio_combo.findData(cur_ram)
            if idx >= 0 and idx != self.rec_audio_combo.currentIndex():
                self.rec_audio_combo.blockSignals(True)
                self.rec_audio_combo.setCurrentIndex(idx)
                self.rec_audio_combo.blockSignals(False)
        self._apply_player_card_style()
        self._relayout_tools()

    def _cover_metrics(self) -> tuple[int, int]:
        cfg = self.config_mgr.config if self.config_mgr is not None else None
        style = str(getattr(cfg, "player_card_style", "mirror") or "mirror").lower() if cfg else "mirror"
        # 1.5x larger cover artwork in expanded view (54 -> 82px, compact 42 -> 64px) with smooth rounded corners
        return (64, 14) if style == "compact" else (82, 18)

    def _refresh_cover_pixmap(self, fallback_ico: str = "music", fallback_accent: str = "") -> None:
        sz, rad = self._cover_metrics()
        qimg = getattr(self, "_last_art_qimg", None)
        has_img = qimg is not None and not qimg.isNull()
        dpr = max(1.0, float(self.art_lbl.devicePixelRatioF() or 1.0)) if has_img else 1.0
        img_key = qimg.cacheKey() if has_img else 0
        acc = fallback_accent or self._accent_hex
        sig = (has_img, img_key, sz, rad, round(dpr, 2), fallback_ico, acc, self._icon_fg)
        if getattr(self, "_last_cover_sig", None) == sig:
            return
        self._last_cover_sig = sig
        if self.art_lbl.width() != sz or self.art_lbl.height() != sz:
            self.art_lbl.setFixedSize(sz, sz)
        if has_img:
            self.art_lbl.setPixmap(_round_cover_pixmap(qimg, sz, rad, dpr=dpr))
            qss = "background: transparent; border: none;"
        else:
            self.art_lbl.setPixmap(get_pixmap(fallback_ico, size=max(24, sz // 2 - 4), color=self._icon_fg, accent=acc))
            qss = f"background: rgba(255,255,255,0.06); border-radius: {rad}px;"
        if getattr(self.art_lbl, "_cached_qss", None) != qss:
            self.art_lbl._cached_qss = qss
            self.art_lbl.setStyleSheet(qss)
        if self.art_lbl.text():
            self.art_lbl.setText("")

    def _apply_player_card_style(self) -> None:
        cfg = self.config_mgr.config if self.config_mgr is not None else None
        style = str(getattr(cfg, "player_card_style", "mirror") or "mirror").lower() if cfg else "mirror"
        acc = self._accent_hex or "#3B82F6"
        show_spec = style != "compact"
        if self.spectrum.isVisible() != show_spec:
            self.spectrum.setVisible(show_spec)
        self._refresh_cover_pixmap()
        if style == "fill":
            qss = (
                f"QFrame#Card {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(18,24,38,225), stop:1 {acc}66); "
                f"border: 1px solid {acc}99; border-radius: 16px; }}"
            )
        elif style == "compact":
            qss = "QFrame#Card { border-radius: 14px; }"
        else:  # mirror
            qss = f"QFrame#Card {{ border: 1px solid {acc}88; border-radius: 16px; }}"
        if getattr(self.media_card, "_cached_qss", None) != qss:
            self.media_card._cached_qss = qss
            self.media_card.setStyleSheet(qss)

    def apply_palette(self, pal: ThemePalette) -> None:
        is_play = bool(self._last_snapshot.active and self._last_snapshot.active.status == "Playing")
        pal_sig = (pal.accent, pal.text_primary, is_play, self._muted)
        if getattr(self, "_last_home_pal_sig", None) == pal_sig:
            return
        self._last_home_pal_sig = pal_sig
        self._accent_hex = pal.accent
        self._icon_fg = pal.text_primary
        self.control_hub.apply_palette(pal)
        self.spectrum.set_accent(pal.accent)
        self._apply_player_card_style()
        self.prev_btn.setIcon(get_icon("prev", size=16, color=self._icon_fg, accent=pal.accent))
        self.next_btn.setIcon(get_icon("next", size=16, color=self._icon_fg, accent=pal.accent))
        from ..theme import contrast_fg
        pfg = contrast_fg(pal.accent)
        self.play_btn.setStyleSheet(
            f"QPushButton {{ min-height: 30px; max-height: 32px; border-radius: 16px; padding: 0px 14px; "
            f"font-weight: 700; background-color: {pal.accent}; color: {pfg}; border: 1px solid {pal.accent}; }}"
            f"QPushButton:hover {{ border: 1.5px solid #FFFFFF; }}"
        )
        self.play_btn.setIcon(get_icon("pause" if is_play else "play", size=15, color=pfg, accent=pfg))
        self.vol_btn.setIcon(get_icon("mute" if self._muted else "volume", size=15, color=self._icon_fg, accent=pal.accent))
        for tid, btn in self.tool_buttons.items():
            ico = self._tool_icon_map.get(tid, "settings")
            btn.setIcon(get_icon(ico, size=14, color=self._icon_fg, accent=pal.accent))
            btn.setIconSize(QSize(14, 14))
        self.refresh_launcher()

    def set_dev_tools_visible(self, show_dev: bool) -> None:
        self._show_dev_tools = bool(show_dev)
        self._relayout_tools()

    def _relayout_tools(self) -> None:
        while self._tools_grid.count():
            self._tools_grid.takeAt(0)
        cfg = self.config_mgr.config if self.config_mgr is not None else None
        hidden_tools = set(getattr(cfg, "home_hidden_tools", []) or []) if cfg else set()
        visible_ids = [
            tid for tid in self._tool_order
            if (tid != "qr_scan" or self._show_dev_tools) and tid not in hidden_tools
        ]
        for tid, btn in self.tool_buttons.items():
            btn.setVisible(tid in visible_ids)
        # Always use a 3-column grid (~170px per tile) so long Russian labels & icons never clip;
        # when 8 tools are visible, span command_bar across 2 columns so rows remain flush and balanced.
        cols = 3
        non_cmd = [tid for tid in visible_ids if tid != "command_bar"]
        last_cnt = len(non_cmd) % cols
        has_cmd = "command_bar" in visible_ids
        for idx, tid in enumerate(non_cmd):
            btn = self.tool_buttons[tid]
            btn.setVisible(True)
            row = idx // cols
            col = idx % cols
            if idx == len(non_cmd) - 1 and col > 0 and not (has_cmd and last_cnt == 1):
                span = cols - col
            else:
                span = 1
            self._tools_grid.addWidget(btn, row, col, 1, span)
        if has_cmd:
            cb_btn = self.tool_buttons["command_bar"]
            cb_btn.setVisible(True)
            if last_cnt == 1:
                self._tools_grid.addWidget(cb_btn, len(non_cmd) // cols, 1, 1, cols - 1)
            else:
                next_row = (len(non_cmd) + cols - 1) // cols
                self._tools_grid.addWidget(cb_btn, next_row, 0, 1, cols)
        for c in range(4):
            self._tools_grid.setColumnStretch(c, 1 if c < cols else 0)

    def set_recording_state(self, active: bool) -> None:
        btn = self.tool_buttons.get("record")
        if btn:
            btn.setText(tr("record_stop") if active else tr("record"))
            btn.setIcon(get_icon("record", size=14, color="#FFFFFF" if active else self._icon_fg, accent="#EF4444"))
            btn.setObjectName("Danger" if active else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_dictating_state(self, active: bool) -> None:
        btn = self.tool_buttons.get("dictate")
        if btn:
            btn.setText(tr("dictate_stop") if active else tr("dictate"))
            btn.setIcon(get_icon("dictate", size=14, color="#FFFFFF" if active else self._icon_fg, accent=self._accent_hex))
            btn.setObjectName("Primary" if active else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _refresh_player_volume(self) -> None:
        hint = self._player_hint or "spotify"
        def _work() -> None:
            try:
                res = self.media_service.bridge.get_app_volume(hint, "default")
                if res.get("found"):
                    self.volume_polled.emit(int(res.get("volume", 100)), bool(res.get("muted")), True)
                    return
                ov = self.media_service.bridge.get_volume("render", "default")
                self.volume_polled.emit(int(ov.get("volume", 50)), bool(ov.get("muted")), False)
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()

    def _apply_polled_volume(self, pct: int, muted: bool, per_app: bool) -> None:
        if self._vol_dragging:
            return
        self._muted = muted
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(max(0, min(100, pct)))
        self.vol_slider.blockSignals(False)
        tag = "Spotify" if (per_app and "spot" in (self._player_hint or "")) else ("App" if per_app else "Sys")
        self.vol_lbl.setText(f"{tag} {pct}%")
        self.vol_btn.setIcon(get_icon("mute" if muted else "volume", size=15, color=self._icon_fg, accent=self._accent_hex))

    def _on_vol_changed(self, value: int) -> None:
        self._muted = value == 0
        self.vol_lbl.setText(f"{value}%")
        now = time.monotonic()
        if now - self._last_vol_send < 0.045:
            return
        self._last_vol_send = now
        self._push_volume(value)

    def _on_vol_released(self) -> None:
        self._vol_dragging = False
        self._push_volume(self.vol_slider.value())

    def _push_volume(self, value: int) -> None:
        hint = self._player_hint or "spotify"
        def _run() -> None:
            ok = self.media_service.bridge.set_app_volume(hint, value, "default")
            if not ok:
                try:
                    self.media_service.bridge.set_volume(value, "render", "default")
                except Exception:
                    pass
        threading.Thread(target=_run, daemon=True).start()

    def _toggle_player_mute(self) -> None:
        self._muted = not self._muted
        target = 0 if self._muted else max(25, self.vol_slider.value() or 65)
        self.vol_slider.setValue(target)
        self._push_volume(target)
        self.vol_btn.setIcon(get_icon("mute" if self._muted else "volume", size=15, color=self._icon_fg, accent=self._accent_hex))

    def update_spectrum(self, bands: list[float]) -> None:
        self.spectrum.set_bands(bands)

    def _elide_media_labels(self) -> None:
        raw_t = getattr(self, "_raw_media_title", "") or ""
        raw_a = getattr(self, "_raw_media_artist", "") or ""
        tw = max(120, self.title_lbl.width()) if self.title_lbl.width() > 20 else 230
        aw = max(140, self.artist_lbl.width()) if self.artist_lbl.width() > 20 else 300
        self.title_lbl.setText(self.title_lbl.fontMetrics().elidedText(raw_t, Qt.TextElideMode.ElideRight, tw))
        self.artist_lbl.setText(self.artist_lbl.fontMetrics().elidedText(raw_a, Qt.TextElideMode.ElideRight, aw))
        self.title_lbl.setToolTip(raw_t)
        self.artist_lbl.setToolTip(raw_a)

    def update_media_state(self, snap: MediaStateSnapshot) -> None:
        self._last_snapshot = snap
        has_spotify = any(
            "spotify" in ((s.source_id or "") + " " + (s.app_id or "")).lower()
            for s in (snap.sessions or [])
        )
        hidden_blocks = set(getattr(self.config_mgr.config, "home_hidden_blocks", []) or []) if self.config_mgr else set()
        want_vis = bool(snap.active is not None or has_spotify) and ("media" not in hidden_blocks)
        if self.media_card.isVisible() != want_vis:
            self.media_card.setVisible(want_vis)
        cfg_style = str(getattr(self.config_mgr.config, "player_card_style", "mirror") or "mirror").lower() if self.config_mgr else "mirror"
        pcard_sig = (cfg_style, self._accent_hex, self._icon_fg, snap.active is not None, want_vis)
        if getattr(self, "_last_pcard_sig", None) != pcard_sig:
            self._last_pcard_sig = pcard_sig
            self._apply_player_card_style()
        # Refresh player selector choices cleanly without clearing user's choice unnecessarily
        cur_pref = self.media_service.preferred_app_id
        session_ids = [s.app_id for s in snap.sessions]
        existing_ids = [self.player_combo.itemData(i) for i in range(self.player_combo.count())]
        if [""] + session_ids != existing_ids:
            self.player_combo.blockSignals(True)
            self.player_combo.clear()
            self.player_combo.addItem("Авто-плеер (Spotify / GSMTC)", "")
            for s in snap.sessions:
                sname = s.source_name or (s.app_id.split("!")[-1].split(".")[0] or s.app_id[:16])
                self.player_combo.addItem(f"{sname} — {s.title[:18]}", s.app_id)
            idx = self.player_combo.findData(cur_pref)
            self.player_combo.setCurrentIndex(max(0, idx))
            self.player_combo.blockSignals(False)

        act = snap.active
        if not act:
            if getattr(self, "_last_home_media_sig", None) != ("none", self._accent_hex):
                self._last_home_media_sig = ("none", self._accent_hex)
                self._last_art_qimg = None
                self._raw_media_title = tr("no_music")
                self._raw_media_artist = "Поддержка Spotify, Яндекс Музыки, YouTube, VK и плееров"
                self._elide_media_labels()
                self.source_btn.hide()
                self._refresh_cover_pixmap("music", self._accent_hex)
                self.pos_lbl.setText("0:00")
                self.dur_lbl.setText("0:00")
                self.play_btn.setText(" Играть")
                from ..theme import contrast_fg
                pfg = contrast_fg(self._accent_hex)
                self.play_btn.setIcon(get_icon("play", size=14, color=pfg, accent=pfg))
                self._duration = 0.0
                self.active_lyric_btn.hide()
                if self._show_lyrics:
                    self.lyrics_list.clear()
            return

        new_hint = (act.source_id or act.app_id or "spotify").split(".")[0].lower()
        if new_hint != self._player_hint:
            self._player_hint = new_hint or "spotify"
            self._refresh_player_volume()

        is_play = act.status == "Playing"
        src_label = act.source_name or act.app_id
        src_ico = "spotify" if act.source_id == "spotify" else "music"
        meta_sig = (
            act.title,
            act.artist,
            act.album,
            src_ico,
            src_label,
            act.accent_color,
            act.art_key,
            act.duration,
            is_play,
            self._accent_hex,
        )
        if getattr(self, "_last_home_media_sig", None) != meta_sig:
            self._last_home_media_sig = meta_sig
            self._raw_media_title = act.title or "Unknown"
            self._raw_media_artist = f"{act.artist}  •  {act.album}".strip(" •")
            self._elide_media_labels()
            self.source_btn.setIcon(get_icon(src_ico, size=14, color="#F2F5FA", accent=act.accent_color or "#1DB954"))
            self.source_btn.setText(f" {src_label}")
            self.source_btn.show()
            self.spectrum.set_accent(act.accent_color)
            qimg = self.media_service.get_art_qimage(act.art_key)
            self._last_art_qimg = qimg if (qimg is not None and not qimg.isNull()) else None
            self._refresh_cover_pixmap(src_ico, act.accent_color)

            self._duration = act.duration
            dur_t = _fmt_time(act.duration)
            if self.dur_lbl.text() != dur_t:
                self.dur_lbl.setText(dur_t)
            from ..theme import contrast_fg
            pfg = contrast_fg(self._accent_hex)
            self.play_btn.setIcon(get_icon("pause" if is_play else "play", size=15, color=pfg, accent=pfg))
            self.play_btn.setText(" Пауза" if is_play else " Играть")

        pos_t = _fmt_time(act.position)
        if self.pos_lbl.text() != pos_t:
            self.pos_lbl.setText(pos_t)

        if not self._seeking and act.duration > 0:
            s_val = int(round(min(1.0, max(0.0, act.position / act.duration)) * 1000))
            if self.seek_slider.value() != s_val:
                self.seek_slider.blockSignals(True)
                self.seek_slider.setValue(s_val)
                self.seek_slider.blockSignals(False)

        lyr_line = (getattr(snap, "active_lyric", "") or "").strip()
        lyr_obj = getattr(snap, "lyrics", None)
        has_lyr = bool(lyr_line or (lyr_obj and (lyr_obj.synced or lyr_obj.plain)))
        if has_lyr:
            disp_l = lyr_line or (
                lyr_obj.synced[0].text
                if (lyr_obj and lyr_obj.synced)
                else (next((ln.strip() for ln in lyr_obj.plain.splitlines() if ln.strip()), "") if (lyr_obj and lyr_obj.plain) else "")
            )
            if disp_l:
                elided_l = self.active_lyric_btn.fontMetrics().elidedText(
                    f"♪  {disp_l}", Qt.TextElideMode.ElideRight, 360
                )
                if self.active_lyric_btn.text() != elided_l:
                    self.active_lyric_btn.setText(elided_l)
                self.active_lyric_btn.setToolTip(disp_l)
                self.active_lyric_btn.show()
            else:
                self.active_lyric_btn.hide()
        else:
            self.active_lyric_btn.hide()

        if self._show_lyrics:
            self._render_lyrics(snap)

    def _on_player_selected(self) -> None:
        app_id = self.player_combo.currentData() or ""
        self.media_service.set_preferred_player(app_id)

    def _on_seek_released(self) -> None:
        self._seeking = False
        if self._duration > 0:
            sec = (self.seek_slider.value() / 1000.0) * self._duration
            self.media_service.seek(sec)

    def _toggle_lyrics(self, checked: bool) -> None:
        self._show_lyrics = checked
        self.lyrics_list.setVisible(checked)
        if checked:
            self._render_lyrics(self._last_snapshot)

    def _render_lyrics(self, snap: MediaStateSnapshot) -> None:
        lyr = snap.lyrics
        if not lyr or (not lyr.synced and not lyr.plain):
            if self.lyrics_list.count() != 1 or self.lyrics_list.item(0).text() != tr("lyrics_empty"):
                self.lyrics_list.clear()
                self.lyrics_list.addItem(tr("lyrics_empty"))
            return
        if lyr.synced:
            key = lyr.track_key
            if self.lyrics_list.property("track_key") != key:
                self.lyrics_list.setProperty("track_key", key)
                self.lyrics_list.clear()
                for line in lyr.synced:
                    item = QListWidgetItem(line.text)
                    item.setData(Qt.ItemDataRole.UserRole, line.time_sec)
                    self.lyrics_list.addItem(item)
            if snap.active:
                idx = active_line_index(lyr.synced, snap.active.position)
                if 0 <= idx < self.lyrics_list.count():
                    self.lyrics_list.setCurrentRow(idx)
                    self.lyrics_list.scrollToItem(self.lyrics_list.item(idx))
        else:
            key = lyr.track_key + "|plain"
            lines_p = [ln.strip() for ln in lyr.plain.splitlines() if ln.strip()]
            if self.lyrics_list.property("track_key") != key:
                self.lyrics_list.setProperty("track_key", key)
                self.lyrics_list.clear()
                for line in lines_p:
                    self.lyrics_list.addItem(line)
            if snap.active and lines_p and snap.active.duration > 1.0 and snap.active.position >= 0.0:
                frac = max(0.0, min(0.999, snap.active.position / snap.active.duration))
                idx = min(len(lines_p) - 1, int(frac * len(lines_p)))
                if 0 <= idx < self.lyrics_list.count():
                    self.lyrics_list.setCurrentRow(idx)
                    self.lyrics_list.scrollToItem(self.lyrics_list.item(idx))

    def _on_lyric_clicked(self, item: QListWidgetItem) -> None:
        t = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(t, (int, float)):
            self.media_service.seek(float(t))

    # ------------------------------------------------------------------ Launcher
    def _ensure_favicon_async(self, item: LauncherItem) -> None:
        tgt = (item.target or "").strip()
        if not tgt or tgt in self._favicon_loading:
            return
        self._favicon_loading.add(tgt)
        dest = _favicon_cache_path(tgt, self._favicon_dir)

        def _work() -> None:
            try:
                ok = fetch_website_favicon(tgt, dest)
                if ok and dest.is_file():
                    self.storage.update_launcher_item(item.id, icon_path=str(dest))
                    self.favicon_ready.emit()
            except Exception:
                pass
            finally:
                self._favicon_loading.discard(tgt)

        threading.Thread(target=_work, daemon=True).start()

    def refresh_launcher(self) -> None:
        items = self.storage.list_launcher_items()
        groups = sorted({it.group_name for it in items if it.group_name})
        self.group_combo.blockSignals(True)
        cur = self.group_combo.currentData() or ""
        self.group_combo.clear()
        self.group_combo.addItem("Все", "")
        for g in groups:
            self.group_combo.addItem(g, g)
        idx = self.group_combo.findData(cur)
        self.group_combo.setCurrentIndex(max(0, idx))
        self.group_combo.blockSignals(False)
        self.group_combo.setVisible(bool(groups))
        self._active_group = self.group_combo.currentData() or ""

        while self.launcher_grid.count():
            child = self.launcher_grid.takeAt(0)
            w = child.widget()
            if w is not None and w is not self.add_btn:
                w.deleteLater()
        self._launcher_bubbles.clear()

        filtered = [it for it in items if not self._active_group or it.group_name == self._active_group]
        for it in filtered:
            qicon, needs_fav = resolve_launcher_qicon(
                it, self._favicon_dir, self._icon_provider, self._icon_fg, self._accent_hex
            )
            if needs_fav:
                self._ensure_favicon_async(it)
            tip = f"{it.title}" + (f"\nHotkey: {it.hotkey}" if it.hotkey else "")
            btn = _AtollAppBubble(tip, accent_hex=self._accent_hex, is_add=False)
            btn.setIcon(qicon)
            btn.clicked.connect(lambda _, target=it.target: self._launch_item(target))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, b=btn, item=it: self._shortcut_menu(b.mapToGlobal(pos), item))
            self._launcher_bubbles.append(btn)

        self.add_btn.set_accent(self._accent_hex)
        self.add_btn.setIcon(_make_plus_orb_icon(self._icon_fg))
        self._relayout_launcher(force=True)

    def _launcher_cols_per_row(self) -> int:
        vp_w = self.launcher_scroll.viewport().width() if hasattr(self, "launcher_scroll") else 0
        avail = vp_w if vp_w >= 160 else 440
        # 78px slot + 2px spacing, keeping 14px safety margin so the rightmost circle never clips
        return max(4, (avail - 14 + 2) // 80)

    def _relayout_launcher(self, *, force: bool = False) -> None:
        cols = self._launcher_cols_per_row()
        if not force and cols == self._launcher_cols and self.launcher_grid.count() > 0:
            return
        self._launcher_cols = cols
        while self.launcher_grid.count():
            self.launcher_grid.takeAt(0)

        widgets: list[QWidget] = [*self._launcher_bubbles, self.add_btn]
        two_rows_cap = 2 * cols
        for idx, w in enumerate(widgets):
            if idx < two_rows_cap:
                row = idx // cols
                col = idx % cols
            else:
                extra = idx - two_rows_cap
                row = extra % 2
                col = cols + (extra // 2)
            self.launcher_grid.addWidget(w, row, col)

        total = len(widgets)
        if total <= cols:
            want_h = 66
        elif total <= two_rows_cap:
            want_h = 148
        else:
            want_h = 158
        if self.launcher_scroll.height() != want_h:
            self.launcher_scroll.setFixedHeight(want_h)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "title_lbl"):
            self._elide_media_labels()
        if hasattr(self, "launcher_scroll"):
            self._relayout_launcher()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(self, "launcher_scroll"):
            self._relayout_launcher(force=True)

    def _on_group_changed(self) -> None:
        self._active_group = self.group_combo.currentData() or ""
        self.refresh_launcher()

    def _add_shortcut(self) -> None:
        dlg = _InstalledAppsDialog(self, accent_hex=self._accent_hex, icon_fg=self._icon_fg)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if dlg.chosen_action == "app" and dlg.selected_entry is not None:
            entry = dlg.selected_entry
            self.storage.add_launcher_item(
                title=entry.title,
                target=entry.target,
                kind="app",
                group_name="",
                icon_path="",
            )
            self.refresh_launcher()
        elif dlg.chosen_action == "file":
            start_dir = os.environ.get("ProgramFiles") or str(Path.home() / "Desktop")
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Выберите программу",
                start_dir,
                "Программы и ярлыки (*.exe *.lnk *.bat *.cmd *.url);;Все файлы (*.*)",
                options=QFileDialog.Option.DontResolveSymlinks,
            )
            if path:
                title = Path(path).stem
                self.storage.add_launcher_item(title=title, target=path, kind="app", group_name="", icon_path="")
                self.refresh_launcher()
        elif dlg.chosen_action == "url":
            url_dlg = _AddShortcutDialog(self, url_mode=True)
            if url_dlg.exec() == QDialog.DialogCode.Accepted:
                raw_target = url_dlg.target_edit.text().strip()
                if not raw_target:
                    return
                if not raw_target.startswith(("http://", "https://", "steam://")):
                    raw_target = f"https://{raw_target}"
                parsed = urllib.parse.urlparse(raw_target)
                host = parsed.netloc or raw_target
                name = url_dlg.name_edit.text().strip() or host.removeprefix("www.")
                self.storage.add_launcher_item(
                    title=name,
                    target=raw_target,
                    kind="url",
                    group_name=url_dlg.group_edit.text().strip(),
                    icon_path="",
                )
                self.refresh_launcher()

    def _shortcut_menu(self, global_pos, item: LauncherItem) -> None:
        from PyQt6.QtWidgets import QInputDialog

        menu = QMenu(self)
        act_open = menu.addAction(f"Открыть «{item.title}»")
        act_icon = menu.addAction("Выбрать свою иконку / фото…")
        act_grp = menu.addAction("Группа…")
        act_hk = menu.addAction("Хоткей…")
        act_del = menu.addAction("Удалить")
        chosen = menu.exec(global_pos)
        if chosen == act_open:
            self._launch_item(item.target)
        elif chosen == act_icon:
            img_path, _ = QFileDialog.getOpenFileName(
                self,
                "Выберите картинку для круглой иконки",
                str(Path.home() / "Pictures"),
                "Изображения (*.png *.jpg *.jpeg *.webp *.bmp *.ico);;Все файлы (*.*)",
            )
            if img_path:
                self.storage.update_launcher_item(item.id, icon_path=img_path)
                self.refresh_launcher()
        elif chosen == act_grp:
            grp, ok = QInputDialog.getText(self, "Group / Folder", "Group name:", text=item.group_name)
            if ok:
                self.storage.update_launcher_item(item.id, group_name=grp.strip())
                self.refresh_launcher()
        elif chosen == act_hk:
            hk, ok = QInputDialog.getText(self, "Shortcut Hotkey", "Hotkey (e.g. Ctrl+Alt+1):", text=item.hotkey)
            if ok:
                self.storage.update_launcher_item(item.id, hotkey=hk.strip())
                self.refresh_launcher()
                self.tool_triggered.emit("reload_hotkeys")
        elif chosen == act_del:
            self.storage.delete_launcher_item(item.id)
            self.refresh_launcher()

    @staticmethod
    def _launch_item(target: str) -> None:
        try:
            if target.startswith(("http://", "https://", "steam://", "ms-settings:", "shell:")):
                try:
                    os.startfile(target)  # type: ignore[attr-defined]
                except Exception:
                    if target.startswith("shell:"):
                        subprocess.Popen(["explorer.exe", target])
            elif Path(target).exists():
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(target, shell=True)
        except Exception:
            pass
