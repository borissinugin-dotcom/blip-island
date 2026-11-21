"""Cross-browser detection and launcher for external AI web chats (Edge, Chrome, Yandex, Firefox, System Default)."""

from __future__ import annotations

import ctypes
import os
import shlex
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == "win32":  # pragma: no cover - runtime Win32 API signatures
    import ctypes.wintypes as wt

    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32
    _gdi32 = ctypes.windll.gdi32
    _dwm = ctypes.windll.dwmapi
    _u32.IsWindow.argtypes = [wt.HWND]
    _u32.IsWindow.restype = wt.BOOL
    _u32.IsWindowVisible.argtypes = [wt.HWND]
    _u32.IsWindowVisible.restype = wt.BOOL
    _u32.IsIconic.argtypes = [wt.HWND]
    _u32.IsIconic.restype = wt.BOOL
    _u32.IsZoomed.argtypes = [wt.HWND]
    _u32.IsZoomed.restype = wt.BOOL
    _u32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    _u32.GetWindow.restype = wt.HWND
    _u32.GetForegroundWindow.argtypes = []
    _u32.GetForegroundWindow.restype = wt.HWND
    _u32.SetForegroundWindow.argtypes = [wt.HWND]
    _u32.SetForegroundWindow.restype = wt.BOOL
    _u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    _u32.ShowWindow.restype = wt.BOOL
    _u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    _u32.GetWindowRect.restype = wt.BOOL
    _u32.SetWindowPos.argtypes = [
        wt.HWND,
        wt.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wt.UINT,
    ]
    _u32.SetWindowPos.restype = wt.BOOL
    _u32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    _u32.GetWindowLongW.restype = ctypes.c_long
    _u32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
    _u32.SetWindowLongW.restype = ctypes.c_long
    _u32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
    _u32.SetWindowRgn.restype = ctypes.c_int
    _u32.GetSystemMenu.argtypes = [wt.HWND, wt.BOOL]
    _u32.GetSystemMenu.restype = wt.HMENU
    _u32.DeleteMenu.argtypes = [wt.HMENU, wt.UINT, wt.UINT]
    _u32.DeleteMenu.restype = wt.BOOL
    _u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    _u32.PostMessageW.restype = wt.BOOL
    _u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wt.DWORD
    _u32.IsChild.argtypes = [wt.HWND, wt.HWND]
    _u32.IsChild.restype = wt.BOOL
    _u32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t]
    _u32.keybd_event.restype = None
    _gdi32.CreateRoundRectRgn.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]
    _gdi32.CreateRoundRectRgn.restype = wt.HRGN
else:
    wt = None  # type: ignore[assignment]
    _u32 = None  # type: ignore[assignment]
    _k32 = None  # type: ignore[assignment]
    _gdi32 = None  # type: ignore[assignment]
    _dwm = None  # type: ignore[assignment]

try:
    import winreg
except Exception:  # pragma: no cover
    winreg = None  # type: ignore[assignment]


@dataclass(frozen=True)
class BrowserInfo:
    """Describes an external web browser installed on the host system."""

    browser_id: str
    name: str
    exe_path: str = ""
    supports_app_mode: bool = True
    is_default: bool = False

    @property
    def available(self) -> bool:
        return bool(self.exe_path and os.path.isfile(self.exe_path))


_BROWSER_SPECS: tuple[dict, ...] = (
    {
        "id": "edge",
        "name": "Microsoft Edge",
        "exes": ("msedge.exe",),
        "progids": ("msedgehtm", "microsoft edge"),
        "always_list": True,
        "app_mode": True,
        "paths": (
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
        ),
    },
    {
        "id": "chrome",
        "name": "Google Chrome",
        "exes": ("chrome.exe",),
        "progids": ("chromehtml", "google chrome"),
        "always_list": True,
        "app_mode": True,
        "paths": (
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
        ),
    },
    {
        "id": "yandex",
        "name": "Яндекс.Браузер",
        "exes": ("browser.exe",),
        "progids": ("yandexbrowser", "yandexhtml", "yabrowser"),
        "always_list": True,
        "app_mode": True,
        "paths": (
            r"%LocalAppData%\Yandex\YandexBrowser\Application\browser.exe",
            r"%ProgramFiles(x86)%\Yandex\YandexBrowser\Application\browser.exe",
            r"%ProgramFiles%\Yandex\YandexBrowser\Application\browser.exe",
        ),
    },
    {
        "id": "brave",
        "name": "Brave",
        "exes": ("brave.exe",),
        "progids": ("bravehtml",),
        "always_list": False,
        "app_mode": True,
        "paths": (
            r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ),
    },
    {
        "id": "opera",
        "name": "Opera",
        "exes": ("opera.exe",),
        "progids": ("operastable", "operagxstable"),
        "always_list": False,
        "app_mode": True,
        "paths": (
            r"%LocalAppData%\Programs\Opera\opera.exe",
            r"%LocalAppData%\Programs\Opera GX\opera.exe",
            r"%ProgramFiles%\Opera\opera.exe",
        ),
    },
    {
        "id": "vivaldi",
        "name": "Vivaldi",
        "exes": ("vivaldi.exe",),
        "progids": ("vivaldihtm",),
        "always_list": False,
        "app_mode": True,
        "paths": (
            r"%LocalAppData%\Vivaldi\Application\vivaldi.exe",
            r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe",
        ),
    },
    {
        "id": "firefox",
        "name": "Mozilla Firefox",
        "exes": ("firefox.exe",),
        "progids": ("firefoxurl",),
        "always_list": False,
        "app_mode": False,
        "paths": (
            r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
            r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe",
            r"%LocalAppData%\Mozilla Firefox\firefox.exe",
        ),
    },
)


def _clean_reg_exe(raw_cmd: str) -> str:
    """Extract executable path from a Windows registry command/AppPath value."""
    txt = (raw_cmd or "").strip()
    if not txt:
        return ""
    if txt.startswith('"'):
        end_q = txt.find('"', 1)
        if end_q > 1:
            cand = os.path.expandvars(txt[1:end_q])
            if os.path.isfile(cand):
                return cand
    cand_direct = os.path.expandvars(txt.strip('"'))
    if os.path.isfile(cand_direct):
        return cand_direct
    try:
        toks = shlex.split(txt, posix=False)
        if toks:
            cand = os.path.expandvars(toks[0].strip('"'))
            if os.path.isfile(cand):
                return cand
    except Exception:
        pass
    return ""


def _query_app_path(exe_name: str) -> str:
    if winreg is None or sys.platform != "win32":
        return ""
    sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, "")
                cand = _clean_reg_exe(str(val or ""))
                if cand:
                    return cand
        except OSError:
            continue
    return ""


def _query_start_menu_browsers() -> dict[str, str]:
    """Scan StartMenuInternet registry keys to catch custom-installed browsers (e.g. Yandex)."""
    found: dict[str, str] = {}
    if winreg is None or sys.platform != "win32":
        return found
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet") as root:
                idx = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, idx)
                        idx += 1
                    except OSError:
                        break
                    try:
                        with winreg.OpenKey(root, rf"{sub_name}\shell\open\command") as cmd_key:
                            cmd_val, _ = winreg.QueryValueEx(cmd_key, "")
                            exe = _clean_reg_exe(str(cmd_val or ""))
                            if exe:
                                found[sub_name.lower()] = exe
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def detect_default_browser_id() -> str:
    """Return browser_id of the Windows default HTTPS handler when recognized."""
    if winreg is None or sys.platform != "win32":
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as k:
            prog_id = str(winreg.QueryValueEx(k, "ProgId")[0] or "").lower()
    except OSError:
        return ""
    for spec in _BROWSER_SPECS:
        if any(h in prog_id for h in spec["progids"]):
            return str(spec["id"])
    return ""


def _find_spec_exe(spec: dict, start_menu_map: dict[str, str]) -> str:
    # 1. Explicit App Paths registry entries
    for exe_name in spec["exes"]:
        cand = _query_app_path(exe_name)
        if cand:
            return cand
    # 2. Expanded standard installation paths
    for raw_path in spec["paths"]:
        cand = os.path.expandvars(raw_path)
        if cand and os.path.isfile(cand):
            return cand
    # 3. StartMenuInternet registered browsers (catches per-user Yandex / custom Chrome builds)
    exe_lows = {e.lower() for e in spec["exes"]}
    for sm_key, sm_exe in start_menu_map.items():
        if Path(sm_exe).name.lower() in exe_lows:
            if spec["id"] != "yandex" or "yandex" in sm_exe.lower() or "yandex" in sm_key:
                return sm_exe
        if any(h in sm_key for h in spec["progids"]):
            return sm_exe
    # 4. PATH lookup (avoid ambiguous browser.exe unless path belongs to Yandex)
    for exe_name in spec["exes"]:
        found = shutil.which(exe_name)
        if found and os.path.isfile(found):
            if spec["id"] != "yandex" or "yandex" in found.lower():
                return found
    return ""


def detect_installed_browsers(only_available: bool = False) -> list[BrowserInfo]:
    """Detect Edge, Chrome, Yandex Browser, and other desktop browsers installed on Windows."""
    default_id = detect_default_browser_id()
    sm_map = _query_start_menu_browsers()
    out: list[BrowserInfo] = []
    for spec in _BROWSER_SPECS:
        exe = _find_spec_exe(spec, sm_map)
        avail = bool(exe and os.path.isfile(exe))
        if only_available and not avail:
            continue
        if not avail and not spec.get("always_list", False):
            continue
        out.append(
            BrowserInfo(
                browser_id=str(spec["id"]),
                name=str(spec["name"]),
                exe_path=exe,
                supports_app_mode=bool(spec["app_mode"]),
                is_default=(spec["id"] == default_id),
            )
        )
    return out


def resolve_browser_for_mode(mode: str = "auto", browsers: list[BrowserInfo] | None = None) -> BrowserInfo | None:
    """Resolve the target external browser with automatic fallback when Chrome/requested browser is missing."""
    items = detect_installed_browsers(only_available=False) if browsers is None else list(browsers)
    avail = [b for b in items if b.available]
    if not avail:
        return None

    m = (mode or "auto").strip().lower()
    if m not in ("", "auto", "system", "webengine"):
        for b in avail:
            if b.browser_id == m:
                return b

    # System default desktop browser if recognized
    for b in avail:
        if b.is_default:
            return b

    # Priority fallback chain when Chrome is absent (Edge is built-in on Windows 10/11)
    for pref_id in ("chrome", "edge", "yandex", "firefox", "brave", "opera", "vivaldi"):
        for b in avail:
            if b.browser_id == pref_id:
                return b
    return avail[0]


def sanitize_ai_url(current_url: str, canonical_url: str) -> str:
    """Prevent re-opening an embedded Google error/rejected auth page in the external browser."""
    raw = (current_url or "").strip()
    if not raw:
        return canonical_url
    try:
        p = urlparse(raw)
    except Exception:
        return canonical_url
    if p.scheme not in ("http", "https") or not p.netloc:
        return canonical_url
    host = p.netloc.lower()
    low = raw.lower()
    if "accounts.google.com" in host or "/signin/rejected" in low or "v3/signin" in low:
        return canonical_url
    return raw


def launch_in_browser(
    url: str,
    mode: str = "auto",
    *,
    app_mode: bool = True,
    browsers: list[BrowserInfo] | None = None,
    logical_bounds: tuple[int, int, int, int] | None = None,
) -> tuple[bool, BrowserInfo | None]:
    """Open URL cleanly in selected/available browser (Chrome, Edge, Yandex app-window or system default)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, None
    safe_url = url.strip()

    m = (mode or "auto").strip().lower()
    if m == "system":
        try:
            if sys.platform == "win32":
                os.startfile(safe_url)  # type: ignore[attr-defined]
                return True, resolve_browser_for_mode("auto", browsers=browsers)
            return webbrowser.open(safe_url), None
        except Exception:
            return webbrowser.open(safe_url), None

    chosen = resolve_browser_for_mode(m if m != "webengine" else "auto", browsers=browsers)
    if chosen is not None and chosen.available:
        if app_mode and chosen.supports_app_mode:
            cmd = [chosen.exe_path, f"--app={safe_url}"]
            if logical_bounds is not None:
                lx, ly, lw, lh = logical_bounds
                cmd.extend([f"--window-position={int(lx)},{int(ly)}", f"--window-size={max(420, int(lw))},{max(320, int(lh))}"])
        else:
            cmd = [chosen.exe_path, safe_url]
        try:
            subprocess.Popen(cmd, close_fds=True)
            return True, chosen
        except Exception:
            pass

    try:
        if sys.platform == "win32":
            os.startfile(safe_url)  # type: ignore[attr-defined]
            return True, chosen
        return webbrowser.open(safe_url), chosen
    except Exception:
        return webbrowser.open(safe_url), chosen


def get_process_exe_name(pid: int) -> str:
    """Return lowercase executable basename (e.g. 'chrome.exe') for a Windows PID."""
    if sys.platform != "win32" or _k32 is None or pid <= 0:
        return ""
    h = _k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        sz = wt.DWORD(512)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
            return Path(buf.value).name.lower()
    except Exception:
        pass
    finally:
        _k32.CloseHandle(h)
    return ""


def enum_chromium_hwnds(exe_basename: str = "") -> dict[int, tuple[str, str, int]]:
    """Return visible Chromium app/browser HWNDs {hwnd: (class_name, title, pid)} filtered cleanly by exe name."""
    out: dict[int, tuple[str, str, int]] = {}
    if sys.platform != "win32" or _u32 is None:
        return out
    want_exe = (exe_basename or "").strip().lower()

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(h: wt.HWND, _lp: wt.LPARAM) -> wt.BOOL:
        if not _u32.IsWindowVisible(h):
            return True
        cls = ctypes.create_unicode_buffer(64)
        _u32.GetClassNameW(h, cls, 64)
        if "Chrome_WidgetWin" not in cls.value:
            return True
        pid = wt.DWORD(0)
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if want_exe:
            pname = get_process_exe_name(int(pid.value))
            if pname != want_exe:
                return True
        title = ctypes.create_unicode_buffer(256)
        _u32.GetWindowTextW(h, title, 256)
        out[int(h)] = (cls.value, title.value, int(pid.value))
        return True

    _u32.EnumWindows(_cb, 0)
    return out


def is_window_alive(hwnd: int) -> bool:
    return bool(sys.platform == "win32" and _u32 is not None and hwnd > 0 and _u32.IsWindow(wt.HWND(hwnd)))


def is_window_visible(hwnd: int) -> bool:
    return bool(is_window_alive(hwnd) and _u32.IsWindowVisible(wt.HWND(hwnd)))


def is_window_below_normal_sibling(hwnd: int, island_hwnd: int = 0) -> bool:
    """Return True if any visible normal window is stacked above hwnd, or if hwnd is not TOPMOST beneath island_hwnd."""
    if sys.platform != "win32" or _u32 is None or not is_window_visible(hwnd):
        return False
    cur = wt.HWND(hwnd)
    saw_island_above = not bool(island_hwnd and is_window_alive(island_hwnd))
    for _ in range(96):
        prev = _u32.GetWindow(cur, 3)  # GW_HWNDPREV
        if not prev:
            break
        cur = prev
        if island_hwnd and int(cur) == int(island_hwnd):
            saw_island_above = True
            continue
        if not _u32.IsWindowVisible(cur) or _u32.IsIconic(cur):
            continue
        ex = int(_u32.GetWindowLongW(cur, -20))
        if ex & 0x00000008:  # WS_EX_TOPMOST
            continue
        r = wt.RECT()
        if _u32.GetWindowRect(cur, ctypes.byref(r)):
            if (r.right - r.left) >= 60 and (r.bottom - r.top) >= 60:
                return True
    if not (int(_u32.GetWindowLongW(wt.HWND(hwnd), -20)) & 0x00000008):
        return True
    if not saw_island_above:
        return True
    return False


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32" or _u32 is None or hwnd <= 0:
        return None
    r = wt.RECT()
    if _u32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
        return int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top)
    return None


def get_foreground_hwnd() -> int:
    if sys.platform != "win32" or _u32 is None:
        return 0
    return int(_u32.GetForegroundWindow() or 0)


def is_hwnd_active_tree(hwnd: int, clipper_hwnd: int = 0, fg: int = 0) -> bool:
    """Return True when fg matches the docked browser, its Clipper host, or any child HWND."""
    if sys.platform != "win32" or _u32 is None:
        return False
    cur = fg or get_foreground_hwnd()
    if not cur:
        return False
    if cur == hwnd or (clipper_hwnd and cur == clipper_hwnd):
        return True
    if clipper_hwnd and is_window_alive(clipper_hwnd) and _u32.IsChild(wt.HWND(clipper_hwnd), wt.HWND(cur)):
        return True
    if hwnd and is_window_alive(hwnd) and _u32.IsChild(wt.HWND(hwnd), wt.HWND(cur)):
        return True
    return False


def configure_docked_browser_hwnd(hwnd: int) -> tuple[int, int] | None:
    """Keep Chrome/Edge native WS_POPUP (for 100% native keyboard input) while stripping caption, frame, taskbar icon, and system menu commands."""
    if sys.platform != "win32" or _u32 is None or not is_window_alive(hwnd):
        return None
    h = wt.HWND(hwnd)
    orig_style = int(_u32.GetWindowLongW(h, -16))
    orig_ex = int(_u32.GetWindowLongW(h, -20))
    # Strip WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU, ensure WS_POPUP (never WS_CHILD)
    new_style = (orig_style & ~(0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000 | 0x40000000)) | 0x80000000
    # Clear WS_EX_APPWINDOW, add WS_EX_TOOLWINDOW | WS_EX_TOPMOST so docked browser stays above normal apps and right behind IslandWindow
    new_ex = (orig_ex | 0x00000080 | 0x00000008) & ~0x00040000
    _u32.SetWindowLongW(h, -16, new_style)
    _u32.SetWindowLongW(h, -20, new_ex)
    hmenu = _u32.GetSystemMenu(h, False)
    if hmenu:
        for sc in (0xF000, 0xF010, 0xF020, 0xF030, 0xF060, 0xF120):
            _u32.DeleteMenu(hmenu, sc, 0)
    if _dwm is not None:
        try:
            corner = ctypes.c_int(2)  # DWMWCP_ROUND
            _dwm.DwmSetWindowAttribute(h, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        except Exception:
            pass
    _u32.SetWindowPos(h, wt.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0020 | 0x0010)
    return orig_style, orig_ex


def restore_browser_hwnd(hwnd: int, orig_style: int, orig_exstyle: int, clipper_hwnd: int = 0) -> None:
    """Restore full window region, system menu, desktop frame, and taskbar button on pop-out."""
    if sys.platform != "win32" or _u32 is None or not is_window_alive(hwnd):
        return
    h = wt.HWND(hwnd)
    _u32.SetWindowRgn(h, None, True)
    _u32.GetSystemMenu(h, True)
    _u32.SetWindowLongW(h, -16, int(orig_style))
    _u32.SetWindowLongW(h, -20, int(orig_exstyle))
    _u32.SetWindowPos(h, wt.HWND(-2), 180, 120, 960, 740, 0x0020 | 0x0040)
    _u32.SetForegroundWindow(h)


def sync_docked_browser_hwnd(
    hwnd: int,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    dpr: float = 1.0,
    visible: bool,
    topmost: bool = False,
    clipper_hwnd: int = 0,
    insert_after_hwnd: int = 0,
) -> bool:
    """Position native top-level Chrome at top of Normal band (HWND_TOP) behind TOPMOST IslandWindow portal."""
    if sys.platform != "win32" or _u32 is None or not is_window_alive(hwnd):
        return False
    wh = wt.HWND(hwnd)
    if not visible:
        _u32.ShowWindow(wh, 0)
        return True
    if _u32.IsZoomed(wh) or _u32.IsIconic(wh):
        _u32.ShowWindow(wh, 9)  # SW_RESTORE

    scale = max(1.0, float(dpr or 1.0))
    crop_top = max(28, int(round(30.0 * scale)))
    side_pad = max(6, int(round(7.0 * scale)))
    vp_w = max(240, int(w))
    vp_h = max(180, int(h))
    win_x = int(x) - side_pad
    win_y = int(y) - crop_top
    win_w = vp_w + 2 * side_pad
    win_h = vp_h + crop_top + side_pad

    # Keep browser TOPMOST right behind IslandWindow so normal windows (Antigravity, Spotify) never overlap the portal
    cur_ex = int(_u32.GetWindowLongW(wh, -20))
    if not (cur_ex & 0x00000008):
        _u32.SetWindowPos(wh, wt.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    flags = 0x0040 | 0x0010  # SWP_SHOWWINDOW | SWP_NOACTIVATE
    after_h = wt.HWND(insert_after_hwnd) if (insert_after_hwnd and is_window_alive(insert_after_hwnd)) else wt.HWND(-1)
    _u32.SetWindowPos(wh, after_h, win_x, win_y, win_w, win_h, flags)
    if insert_after_hwnd and is_window_alive(insert_after_hwnd) and not topmost:
        _u32.SetWindowPos(wt.HWND(insert_after_hwnd), wt.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    if _gdi32 is not None:
        rad = max(10, int(round(16.0 * scale)))
        rgn = _gdi32.CreateRoundRectRgn(side_pad, crop_top, win_w - side_pad + 1, win_h - side_pad + 1, rad, rad)
        if rgn:
            _u32.SetWindowRgn(wh, rgn, True)
    return True


def hide_browser_hwnd(hwnd: int, clipper_hwnd: int = 0) -> None:
    if sys.platform == "win32" and _u32 is not None and is_window_alive(hwnd):
        _u32.ShowWindow(wt.HWND(hwnd), 0)


def close_browser_hwnd(hwnd: int, clipper_hwnd: int = 0) -> None:
    if sys.platform == "win32" and _u32 is not None and is_window_alive(hwnd):
        _u32.PostMessageW(wt.HWND(hwnd), 0x0010, 0, 0)  # WM_CLOSE


def send_browser_nav_key(hwnd: int, kind: str) -> None:
    """Focus docked browser HWND momentarily and send native Back (Alt+Left) or Reload (F5)."""
    if sys.platform != "win32" or _u32 is None or not is_window_alive(hwnd):
        return
    h = wt.HWND(hwnd)
    _u32.SetForegroundWindow(h)
    if kind == "back":
        _u32.keybd_event(0x12, 0, 0, 0)      # VK_MENU down
        _u32.keybd_event(0x25, 0, 0, 0)      # VK_LEFT down
        _u32.keybd_event(0x25, 0, 2, 0)      # VK_LEFT up
        _u32.keybd_event(0x12, 0, 2, 0)      # VK_MENU up
    elif kind == "reload":
        _u32.keybd_event(0x74, 0, 0, 0)      # VK_F5 down
        _u32.keybd_event(0x74, 0, 2, 0)      # VK_F5 up
