"""Win32 API helpers for overlay windows, non-activating focus, clipboard paste, hotkeys, and system actions."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shlex
import subprocess
import sys
import threading
import time
import winreg
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32
advapi32 = ctypes.windll.advapi32

if sys.platform == "win32":
    user32.GetClipboardData.argtypes = [wt.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = wt.BOOL
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wt.SHORT
    user32.IsWindow.argtypes = [wt.HWND]
    user32.IsWindow.restype = wt.BOOL
    user32.IsZoomed.argtypes = [wt.HWND]
    user32.IsZoomed.restype = wt.BOOL
    user32.IsWindowVisible.argtypes = [wt.HWND]
    user32.IsWindowVisible.restype = wt.BOOL
    user32.GetTopWindow.argtypes = [wt.HWND]
    user32.GetTopWindow.restype = wt.HWND
    user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    user32.GetWindow.restype = wt.HWND
    user32.GetWindowTextLengthW.argtypes = [wt.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wt.HWND
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.SetForegroundWindow.restype = wt.BOOL
    user32.SetWindowPos.argtypes = [
        wt.HWND,
        wt.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wt.UINT,
    ]
    user32.SetWindowPos.restype = wt.BOOL
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.LPWSTR,
        ctypes.POINTER(wt.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        ctypes.POINTER(wt.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wt.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wt.HANDLE,
         ctypes.c_uint,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wt.BOOL

VK_LBUTTON = 0x01
SNIPPING_OVERLAY_EXES = {
    "screenclippinghost.exe",
    "snippingtool.exe",
    "screensketch.exe",
    "sharex.exe",
    "lightshot.exe",
    "flameshot.exe",
}
_FULLSCREEN_EXEMPT_EXES = SNIPPING_OVERLAY_EXES | {
    "explorer.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "discord.exe",
    "discordptb.exe",
    "discordcanary.exe",
}

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000

WM_SYSCOMMAND = 0x0112
SC_MAXIMIZE = 0xF030
WM_APPCOMMAND = 0x0319
SW_RESTORE = 9

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_V = 0x56
VK_CAPITAL = 0x14

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TokenElevation = 20

SPI_SETMOUSESONAR = 0x101D
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 28)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", wt.POINT),
    ]


@dataclass
class ForegroundWindowInfo:
    hwnd: int
    pid: int
    exe_name: str
    title: str
    is_fullscreen: bool
    is_elevated: bool
    is_maximized: bool = False


def set_app_user_model_id(app_id: str) -> None:
    """Register an explicit Windows AppUserModelID for proper Taskbar grouping and icon display."""
    if sys.platform != "win32" or not app_id:
        return
    try:
        shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


def set_overlay_window_flags(
    hwnd: int,
    click_through: bool = False,
    *,
    no_activate: bool = True,
    app_window: bool = False,
) -> None:
    """Configure an overlay window's topmost, taskbar presence, focus, and click-through flags in-place."""
    if not hwnd or sys.platform != "win32":
        return
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TOPMOST
    if app_window:
        style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
    else:
        style = (style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
    if no_activate:
        style |= WS_EX_NOACTIVATE
    else:
        style &= ~WS_EX_NOACTIVATE
    if click_through:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def enum_visible_process_windows(
    process_names: set[str] | None = None,
    *,
    include_hidden_players: bool = False,
) -> list[tuple[int, str, str]]:
    """Return list of (hwnd, exe_name_lower, title) for top-level windows using thread-safe HWND walking."""
    if sys.platform != "win32":
        return []
    wanted = {p.lower() for p in process_names} if process_names else None
    tray_player_exes = {"spotify.exe", "yandexmusic.exe", "яндекс музыка.exe", "aimp.exe", "winamp.exe"}
    ignore_titles = {
        "default ime", "msctfime ui", "gdi+ window", "chrome legacy window",
        "DDE Server Window".lower(), "CicMarshalWnd".lower(),
    }
    results: list[tuple[int, str, str]] = []
    hidden_fallbacks: list[tuple[int, str, str]] = []
    GW_HWNDNEXT = 2
    seen: set[int] = set()
    try:
        hwnd = user32.GetTopWindow(None)
        steps = 0
        while hwnd and steps < 4096:
            steps += 1
            ih = int(hwnd)
            if ih in seen:
                break
            seen.add(ih)
            if user32.IsWindow(hwnd):
                vis = bool(user32.IsWindowVisible(hwnd))
                if vis or include_hidden_players:
                    length = int(user32.GetWindowTextLengthW(hwnd) or 0)
                    if 0 < length <= 1024:
                        pid = wt.DWORD(0)
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value:
                            exe, _ = _get_process_name_and_elevation(pid.value)
                            if exe and (wanted is None or exe in wanted):
                                if vis or exe in tray_player_exes:
                                    buf = ctypes.create_unicode_buffer(length + 2)
                                    user32.GetWindowTextW(hwnd, buf, length + 2)
                                    title = buf.value.strip()
                                    if title and title.lower() not in ignore_titles:
                                        if vis:
                                            results.append((ih, exe, title))
                                        else:
                                            hidden_fallbacks.append((ih, exe, title))
            hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
    except Exception:
        pass
    if hidden_fallbacks:
        results.extend(hidden_fallbacks)
    return results


_PROC_NAMES_LOCK = threading.Lock()
_PROC_NAMES_CACHE: tuple[float, set[str]] = (0.0, set())
_PID_INFO_LOCK = threading.Lock()
_PID_INFO_CACHE: dict[int, tuple[float, str, bool]] = {}
_DESKTOP_HWND_CACHE: tuple[int, int] = (0, 0)


def set_process_below_normal(pid: int) -> bool:
    """Lower background helper process priority so it never causes foreground/game microstutters."""
    if sys.platform != "win32" or not pid or pid <= 4:
        return False
    PROCESS_SET_INFORMATION = 0x0200
    BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
    handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        return bool(kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS))
    except Exception:
        return False
    finally:
        kernel32.CloseHandle(handle)


def list_running_process_names(max_age_s: float = 1.2) -> set[str]:
    """Return lowercased running process exe names using one cached Win32 Toolhelp32 snapshot."""
    global _PROC_NAMES_CACHE
    if sys.platform != "win32":
        return set()
    now = time.monotonic()
    with _PROC_NAMES_LOCK:
        cached_ts, cached_names = _PROC_NAMES_CACHE
        if cached_names and (now - cached_ts) <= max_age_s:
            return cached_names

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    names: set[str] = set()
    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap not in (0, -1, ctypes.c_void_p(-1).value):
        try:
            pe = _PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    nm = (pe.szExeFile or "").strip().lower()
                    if nm:
                        names.add(nm)
                    if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            kernel32.CloseHandle(snap)
    with _PROC_NAMES_LOCK:
        _PROC_NAMES_CACHE = (now, names)
    return names


def get_fast_process_table() -> list[tuple[int, str, int, float]] | None:
    """Query all processes (pid, name, cpu_100ns, ws_mb) in one NtQuerySystemInformation(5) call (~5ms, 0 OpenProcess)."""
    if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    try:
        ntdll = ctypes.windll.ntdll

        class _UNICODE_STRING(ctypes.Structure):
            _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT), ("Buffer", ctypes.c_void_p)]

        class _SPI(ctypes.Structure):
            _fields_ = [
                ("NextEntryOffset", wt.ULONG),
                ("NumberOfThreads", wt.ULONG),
                ("WorkingSetPrivateSize", ctypes.c_int64),
                ("HardFaultCount", wt.ULONG),
                ("NumberOfThreadsHighWatermark", wt.ULONG),
                ("CycleTime", ctypes.c_uint64),
                ("CreateTime", ctypes.c_int64),
                ("UserTime", ctypes.c_int64),
                ("KernelTime", ctypes.c_int64),
                ("ImageName", _UNICODE_STRING),
                ("BasePriority", wt.LONG),
                ("UniqueProcessId", ctypes.c_void_p),
                ("InheritedFromUniqueProcessId", ctypes.c_void_p),
                ("HandleCount", wt.ULONG),
                ("SessionId", wt.ULONG),
                ("UniqueProcessKey", ctypes.c_void_p),
                ("PeakVirtualSize", ctypes.c_size_t),
                ("VirtualSize", ctypes.c_size_t),
                ("PageFaultCount", wt.ULONG),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
            ]

        sz = wt.ULONG(512 * 1024)
        buf = (ctypes.c_ubyte * sz.value)()
        for _ in range(4):
            st = ntdll.NtQuerySystemInformation(5, buf, sz.value, ctypes.byref(sz))
            if st == -1073741820 or (st & 0xFFFFFFFF) == 0xC0000004:
                buf = (ctypes.c_ubyte * (sz.value + 65536))()
                sz.value = len(buf)
            else:
                break
        if st != 0:
            return None

        base_addr = ctypes.addressof(buf)
        off = 0
        out: list[tuple[int, str, int, float]] = []
        proc_names: set[str] = set()
        while True:
            spi = _SPI.from_address(base_addr + off)
            pid = int(spi.UniqueProcessId or 0)
            if spi.ImageName.Length and spi.ImageName.Buffer:
                name = ctypes.wstring_at(spi.ImageName.Buffer, spi.ImageName.Length // 2)
            else:
                name = "Idle" if pid == 0 else "?"
            if name:
                proc_names.add(name.lower())
            if pid > 4:
                cpu_100ns = int(spi.UserTime) + int(spi.KernelTime)
                ws_mb = float(spi.WorkingSetSize) / (1024.0 * 1024.0)
                out.append((pid, name, cpu_100ns, ws_mb))
            if spi.NextEntryOffset == 0:
                break
            off += spi.NextEntryOffset

        with _PROC_NAMES_LOCK:
            global _PROC_NAMES_CACHE
            _PROC_NAMES_CACHE = (time.monotonic(), proc_names)
        return out
    except Exception:
        return None


def is_process_running(exe_name: str) -> bool:
    """Return True if a Windows process matching exe_name (e.g. 'spotify.exe') is currently alive."""
    if sys.platform != "win32" or not exe_name:
        return False
    target = exe_name.strip().lower()

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap in (0, -1, ctypes.c_void_p(-1).value):
        return False
    try:
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return False
        while True:
            if (pe.szExeFile or "").lower() == target:
                return True
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    except Exception:
        return False
    finally:
        kernel32.CloseHandle(snap)
    return False


def find_spotify_window_track() -> tuple[int, str, str, str] | None:
    """Inspect running Spotify.exe window to detect playback when GSMTC is unavailable or lagging.

    Returns (hwnd, status, artist, title) or None if Spotify isn't running.
    """
    wins = enum_visible_process_windows({"spotify.exe"})
    if not wins:
        return None
    hwnd, _, win_title = wins[0]
    low = win_title.lower()
    if low in ("spotify", "spotify free", "spotify premium"):
        return hwnd, "Paused", "", ""
    if " - " in win_title:
        artist, track = win_title.split(" - ", 1)
        return hwnd, "Playing", artist.strip(), track.strip()
    return hwnd, "Paused", "", win_title


def send_media_app_command(hwnd: int, action: str) -> bool:
    """Send WM_APPCOMMAND media key directly to a player window HWND (e.g. Spotify.exe)."""
    if sys.platform != "win32":
        return False
    cmd_map = {
        "next": 11,
        "prev": 12,
        "stop": 13,
        "play_pause": 14,
    }
    cmd = cmd_map.get(action)
    if cmd is None:
        return False
    target = hwnd if hwnd and user32.IsWindow(hwnd) else user32.GetForegroundWindow()
    if not target:
        return False
    lparam = (cmd << 16)
    return bool(user32.PostMessageW(target, WM_APPCOMMAND, target, lparam))


def bring_window_to_front(hwnd: int) -> bool:
    if not hwnd or sys.platform != "win32" or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(wt.HWND(hwnd), SW_RESTORE)
    return bool(user32.SetForegroundWindow(wt.HWND(hwnd)))


def raise_hwnd_topmost(hwnd: int) -> None:
    """Keep a floating dialog/picker reliably above TOPMOST Dynamic Island windows."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        user32.SetWindowPos(
            wt.HWND(hwnd),
            wt.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


def set_exclude_from_capture(hwnd: int, exclude: bool) -> bool:
    """Hide or show the window in screenshots, screen recordings, and streams."""
    if not hwnd or sys.platform != "win32":
        return False
    affinity = WDA_EXCLUDEFROMCAPTURE if exclude else WDA_NONE
    return bool(user32.SetWindowDisplayAffinity(hwnd, affinity))


def _get_process_name_and_elevation(pid: int) -> tuple[str, bool]:
    now = time.monotonic()
    with _PID_INFO_LOCK:
        cached = _PID_INFO_CACHE.get(pid)
        if cached is not None and (now - cached[0]) <= 4.0:
            return cached[1], cached[2]

    exe_name = ""
    elevated = False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return exe_name, elevated
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            exe_name = Path(buf.value).name.lower()
        tok = wt.HANDLE()
        if advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(tok)):
            try:
                val = wt.DWORD(0)
                ret_len = wt.DWORD(0)
                if advapi32.GetTokenInformation(
                    tok, TokenElevation, ctypes.byref(val), ctypes.sizeof(val), ctypes.byref(ret_len)
                ):
                    elevated = bool(val.value)
            finally:
                kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(handle)

    if exe_name:
        with _PID_INFO_LOCK:
            if len(_PID_INFO_CACHE) > 256:
                _PID_INFO_CACHE.clear()
            _PID_INFO_CACHE[pid] = (now, exe_name, elevated)
    return exe_name, elevated


def get_foreground_window_info() -> ForegroundWindowInfo | None:
    """Inspect the current foreground window (HWND, process name, fullscreen, elevation)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    if length > 0:
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
    exe_name, elevated = _get_process_name_and_elevation(pid.value)

    rect = wt.RECT()
    is_fs = False
    other_proc = int(pid.value) != os.getpid()
    is_max = bool(user32.IsZoomed(hwnd)) if other_proc else False
    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        mon = user32.MonitorFromWindow(hwnd, 2)
        if mon:
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wt.DWORD),
                    ("rcMonitor", wt.RECT),
                    ("rcWork", wt.RECT),
                    ("dwFlags", wt.DWORD),
                ]

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
                mr = mi.rcMonitor
                if (
                    rect.left <= mr.left
                    and rect.top <= mr.top
                    and rect.right >= mr.right
                    and rect.bottom >= mr.bottom
                    and other_proc
                    and exe_name not in _FULLSCREEN_EXEMPT_EXES
                ):
                    is_fs = True
                elif not is_max and not is_fs and other_proc and exe_name not in _FULLSCREEN_EXEMPT_EXES and title_buf.value:
                    wr = mi.rcWork
                    work_w = wr.right - wr.left
                    work_h = wr.bottom - wr.top
                    if (
                        work_w >= 200
                        and work_h >= 200
                        and (rect.right - rect.left) >= work_w - 16
                        and (rect.bottom - rect.top) >= work_h - 16
                        and rect.left <= wr.left + 8
                        and rect.top <= wr.top + 8
                    ):
                        is_max = True
    return ForegroundWindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        exe_name=exe_name,
        title=title_buf.value,
        is_fullscreen=is_fs,
        is_elevated=elevated,
        is_maximized=is_max,
    )


def is_left_mouse_button_down() -> bool:
    """Return True if the primary mouse button is currently pressed physically."""
    if sys.platform != "win32":
        return False
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def is_snipping_overlay_active() -> bool:
    """Return True while Windows Snipping Tool / Print Screen capture overlay is foreground."""
    fg = get_foreground_window_info()
    if fg is None:
        return False
    low_title = fg.title.lower()
    return fg.exe_name in SNIPPING_OVERLAY_EXES or "screen snipping" in low_title or "набросок" in low_title


def send_ctrl_v() -> None:
    """Synthesize Ctrl+V keystroke via Win32 SendInput."""
    inputs = (_INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].u.ki = _KEYBDINPUT(VK_CONTROL, 0, 0, 0, 0)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].u.ki = _KEYBDINPUT(VK_V, 0, 0, 0, 0)
    inputs[2].type = INPUT_KEYBOARD
    inputs[2].u.ki = _KEYBDINPUT(VK_V, 0, KEYEVENTF_KEYUP, 0, 0)
    inputs[3].type = INPUT_KEYBOARD
    inputs[3].u.ki = _KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, 0)
    user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(_INPUT))


def send_unicode_text(text: str) -> None:
    """Type arbitrary Unicode text into the focused window via SendInput."""
    if not text:
        return
    # Encode as UTF-16LE code units so surrogate pairs work safely
    encoded = text.encode("utf-16-le")
    units = [int.from_bytes(encoded[i : i + 2], "little") for i in range(0, len(encoded), 2)]
    arr = (_INPUT * (len(units) * 2))()
    idx = 0
    for code in units:
        arr[idx].type = INPUT_KEYBOARD
        arr[idx].u.ki = _KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)
        idx += 1
        arr[idx].type = INPUT_KEYBOARD
        arr[idx].u.ki = _KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)
        idx += 1
    user32.SendInput(idx, ctypes.byref(arr), ctypes.sizeof(_INPUT))


def paste_to_window(target_hwnd: int) -> bool:
    """Restore target foreground window if needed and send Ctrl+V."""
    if not target_hwnd:
        return False
    if user32.IsWindow(target_hwnd):
        current = user32.GetForegroundWindow()
        if current != target_hwnd:
            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.04)
        send_ctrl_v()
        return True
    return False


def is_clipboard_marked_private() -> bool:
    """Detect Windows privacy clipboard formats set by password managers."""
    if sys.platform != "win32" or not user32.OpenClipboard(0):
        return False
    try:
        for fmt_name in (
            "ExcludeClipboardContentFromMonitorProcessing",
            "CanIncludeInClipboardHistory",
        ):
            fmt = user32.RegisterClipboardFormatW(fmt_name)
            if fmt and user32.IsClipboardFormatAvailable(fmt):
                if fmt_name == "ExcludeClipboardContentFromMonitorProcessing":
                    return True
                h_data = user32.GetClipboardData(fmt)
                if h_data:
                    sz = kernel32.GlobalSize(h_data)
                    if sz < ctypes.sizeof(wt.DWORD):
                        continue
                    ptr = kernel32.GlobalLock(h_data)
                    if ptr:
                        try:
                            val = ctypes.cast(ptr, ctypes.POINTER(wt.DWORD)).contents.value
                            if val == 0:
                                return True
                        finally:
                            kernel32.GlobalUnlock(h_data)
    except Exception:
        return False
    finally:
        user32.CloseClipboard()
    return False


def get_keyboard_layout_code() -> str:
    """Return short language code (e.g. 'RU', 'EN') of the foreground window's layout."""
    hwnd = user32.GetForegroundWindow()
    tid = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
    hkl = user32.GetKeyboardLayout(tid)
    lang_id = hkl & 0xFFFF
    primary = lang_id & 0x03FF
    mapping = {
        0x19: "RU",
        0x09: "EN",
        0x22: "UK",
        0x07: "DE",
        0x0C: "FR",
        0x0A: "ES",
        0x10: "IT",
        0x15: "PL",
    }
    if primary in mapping:
        return mapping[primary]
    buf = ctypes.create_unicode_buffer(16)
    if kernel32.GetLocaleInfoW(lang_id, 0x0059, buf, len(buf)):  # LOCALE_SISO639LANGNAME
        return buf.value.upper()[:2]
    return f"{primary:02X}"


def is_caps_lock_on() -> bool:
    return bool(user32.GetKeyState(VK_CAPITAL) & 1)


def get_idle_seconds() -> float:
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    ticks = kernel32.GetTickCount64()
    elapsed = max(0, ticks - lii.dwTime)
    return elapsed / 1000.0


def is_windows_dark_theme() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(val) == 0
    except OSError:
        return True


def toggle_windows_dark_theme() -> bool:
    """Toggle Windows Apps & System dark/light theme and return the new is_dark state."""
    new_light = 1 if is_windows_dark_theme() else 0
    path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, new_light)
        winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, new_light)
    user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        "ImmersiveColorSet",
        SMTO_ABORTIFHUNG,
        500,
        None,
    )
    return new_light == 0


_NIGHT_LIGHT_REG_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate"
)


def is_windows_night_light_enabled() -> bool:
    """Return True if Windows Night Light ('Ночной свет') is currently enabled."""
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _NIGHT_LIGHT_REG_PATH) as key:
            data, _ = winreg.QueryValueEx(key, "Data")
            if isinstance(data, (bytes, bytearray)) and len(data) >= 25:
                return data[18] == 0x15 or (data[23] == 0x10 and data[24] == 0x00)
    except Exception:
        pass
    return False


def set_windows_night_light(enabled: bool) -> bool:
    """Enable or disable Windows Night Light ('Ночной свет') directly in Windows settings."""
    if sys.platform != "win32":
        return bool(enabled)
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _NIGHT_LIGHT_REG_PATH,
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            raw, _ = winreg.QueryValueEx(key, "Data")
            data = bytearray(raw)
            if len(data) < 25:
                return False
            currently_on = data[18] == 0x15 or (data[23] == 0x10 and data[24] == 0x00)
            if bool(enabled) == currently_on:
                return currently_on

            if enabled:
                out = bytearray(data[:23]) + bytearray([0x10, 0x00]) + bytearray(data[23:])
                out[18] = 0x15
            else:
                out = bytearray(data[:23]) + bytearray(data[25:])
                out[18] = 0x13

            # Bump CloudStore change counter in bytes 10..14 so Windows applies immediately
            for idx in range(10, 15):
                if out[idx] != 0xFF:
                    out[idx] = (out[idx] + 1) & 0xFF
                    break
                out[idx] = 0x00

            winreg.SetValueEx(key, "Data", 0, winreg.REG_BINARY, bytes(out))
            return bool(enabled)
    except Exception:
        return False


_AUTOSTART_NAME = "BorhDynamicIsland"
_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_APPROVED_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_STARTUP_APPROVED_FOLDER_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"


def _startup_lnk_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / f"{_AUTOSTART_NAME}.lnk"


def build_autostart_command(proj_root: Path, *, autostart_flag: bool = True) -> str:
    """Build a reliable GUI-mode (pythonw.exe / frozen exe) autostart command string."""
    flag = " --autostart" if autostart_flag else ""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"{flag}'
    exe_p = Path(sys.executable)
    pw = exe_p.with_name("pythonw.exe")
    use_exe = str(pw if pw.is_file() else exe_p)
    script = str(Path(proj_root).resolve() / "main.py")
    return f'"{use_exe}" "{script}"{flag}'


def _parse_autostart_command(command: str) -> tuple[str, str, str]:
    """Split autostart command into (executable, arguments, working_directory)."""
    tokens = [t.strip('"') for t in shlex.split(command.strip(), posix=False) if t.strip()]
    if not tokens:
        return "", "", ""
    exe = tokens[0]
    arg_parts = [f'"{t}"' if (" " in t or "\\" in t or ":" in t) else t for t in tokens[1:]]
    args_str = " ".join(arg_parts)
    wd = ""
    for t in tokens[1:]:
        if t.lower().endswith(".py") and Path(t).exists():
            wd = str(Path(t).resolve().parent)
            break
    if not wd and exe:
        wd = str(Path(exe).resolve().parent)
    return exe, args_str, wd


def _sync_startup_shortcut(enabled: bool, command: str) -> bool:
    """Create or delete a Shell:Startup .lnk shortcut (requires no elevation, supports explicit WorkingDirectory)."""
    lnk_path = _startup_lnk_path()
    lnk_name = lnk_path.name
    if not enabled:
        try:
            lnk_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED_FOLDER_SUBKEY) as fk:
                try:
                    winreg.DeleteValue(fk, lnk_name)
                except FileNotFoundError:
                    pass
        except Exception:
            pass
        return True

    exe, args_str, wd = _parse_autostart_command(command)
    if not exe:
        return False
    lnk_path.parent.mkdir(parents=True, exist_ok=True)
    ps_esc = lambda s: "'" + str(s).replace("'", "''") + "'"
    ps_script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut({ps_esc(str(lnk_path))}); "
        f"$sc.TargetPath = {ps_esc(exe)}; "
        f"$sc.Arguments = {ps_esc(args_str)}; "
        f"$sc.WorkingDirectory = {ps_esc(wd)}; "
        "$sc.WindowStyle = 7; "
        "$sc.Description = 'Dynamic Island Borh-edition'; "
        "$sc.Save()"
    )
    ok_lnk = False
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True,
            timeout=5.0,
            creationflags=0x08000000,
        )
        ok_lnk = (res.returncode == 0 and lnk_path.is_file())
    except Exception:
        ok_lnk = False

    if ok_lnk:
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED_FOLDER_SUBKEY) as fk:
                winreg.SetValueEx(fk, lnk_name, 0, winreg.REG_BINARY, b"\x02" + b"\x00" * 11)
        except Exception:
            pass
    return ok_lnk


def set_autostart(enabled: bool, command: str) -> bool:
    """Register or remove Windows logon autostart across HKCU Run, StartupApproved, and Shell:Startup shortcut."""
    ok_reg = False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_SUBKEY) as key:
            if enabled:
                winreg.SetValueEx(key, _AUTOSTART_NAME, 0, winreg.REG_SZ, command)
                ok_reg = True
            else:
                try:
                    winreg.DeleteValue(key, _AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
                ok_reg = True
    except Exception:
        ok_reg = False

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _STARTUP_APPROVED_RUN_SUBKEY) as ak:
            if enabled:
                # 12-byte enabled marker (2 = enabled) prevents Task Manager from blocking Run entry
                winreg.SetValueEx(ak, _AUTOSTART_NAME, 0, winreg.REG_BINARY, b"\x02" + b"\x00" * 11)
            else:
                try:
                    winreg.DeleteValue(ak, _AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
    except Exception:
        pass

    ok_lnk = _sync_startup_shortcut(bool(enabled), command)
    return bool(ok_reg and ok_lnk)


def lock_workstation() -> None:
    user32.LockWorkStation()


def sleep_computer() -> None:
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def restart_computer() -> None:
    subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=0x08000000)


def shutdown_computer() -> None:
    subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=0x08000000)


def _find_desktop_defview() -> tuple[int, int]:
    """Return (SHELLDLL_DefView HWND, SysListView32 HWND) for desktop icons."""
    global _DESKTOP_HWND_CACHE
    c_dv, c_lv = _DESKTOP_HWND_CACHE
    if c_lv and user32.IsWindow(c_lv):
        return c_dv, c_lv
    progman = user32.FindWindowW("Progman", None)
    defview = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None) if progman else 0
    if not defview:
        found: list[int] = []
        enum_cb = WNDENUMPROC(
            lambda hwnd, _lp: (
                found.append(hwnd) or False
                if user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
                else True
            )
        )
        user32.EnumWindows(enum_cb, 0)
        if found:
            defview = user32.FindWindowExW(found[0], 0, "SHELLDLL_DefView", None)
    listview = user32.FindWindowExW(defview, 0, "SysListView32", None) if defview else 0
    _DESKTOP_HWND_CACHE = (int(defview or 0), int(listview or 0))
    return _DESKTOP_HWND_CACHE


def is_desktop_clean() -> bool:
    """Return True if desktop icons (SysListView32) are currently hidden."""
    try:
        _dv, lv = _find_desktop_defview()
        if lv:
            return not bool(user32.IsWindowVisible(lv))
    except Exception:
        pass
    return False


def show_desktop_clean() -> bool:
    """Toggle hiding/restoring desktop icons via SHELLDLL_DefView WM_COMMAND (0x7402)."""
    try:
        dv, lv = _find_desktop_defview()
        if dv:
            user32.SendMessageTimeoutW(dv, 0x0111, 0x7402, 0, SMTO_ABORTIFHUNG, 500, None)
            if lv:
                return not bool(user32.IsWindowVisible(lv))
    except Exception:
        pass
    return is_desktop_clean()


def find_amnezia_vpn_exe() -> Path | None:
    """Locate any installed VPN client executable (Happ, Hiddify/Haiku, AmneziaVPN, v2rayN, etc.)."""
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roam = Path(os.environ.get("APPDATA", ""))
    home = Path.home()

    vpn_specs = [
        ("Happ", "Happ.exe"),
        ("Happ", "happ.exe"),
        ("Hiddify", "Hiddify.exe"),
        ("HiddifyN", "HiddifyN.exe"),
        ("Haiku", "Haiku.exe"),
        ("AmneziaVPN", "AmneziaVPN.exe"),
        ("v2rayN", "v2rayN.exe"),
        ("nekoray", "nekoray.exe"),
        ("nekobox", "nekobox.exe"),
        ("Clash Verge", "clash-verge.exe"),
        ("Throne", "Throne.exe"),
        ("WireGuard", "wireguard.exe"),
        ("OpenVPN", "openvpn-gui.exe"),
        ("Outline", "Outline.exe"),
        ("Proton", "ProtonVPN.exe"),
        ("Mullvad VPN", "Mullvad VPN.exe"),
    ]
    roots = [pf, pf86, local / "Programs", local, roam, home / "Desktop", home / "Downloads"]
    for folder, exe_name in vpn_specs:
        for root in roots:
            if not str(root):
                continue
            cand = root / folder / exe_name
            if cand.exists():
                return cand
            cand_flat = root / exe_name
            if cand_flat.exists():
                return cand_flat
    return None


def toggle_amnezia_vpn_async(done_cb=None) -> bool:
    """Open or toggle AmneziaVPN (Connect/Disconnect) without freezing UI."""
    exe = find_amnezia_vpn_exe()
    if exe is None:
        return False

    def _run() -> None:
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=0x08000000)
            proc_name = exe.stem
            ps = (
                "$ErrorActionPreference = 'SilentlyContinue'\n"
                "Add-Type -AssemblyName UIAutomationClient\n"
                "Add-Type -AssemblyName UIAutomationTypes\n"
                "Start-Sleep -Milliseconds 350\n"
                f"$procs = Get-Process -Name '{proc_name}' -ErrorAction SilentlyContinue\n"
            ) + r"""
foreach ($pr in $procs) {
    if ($pr.MainWindowHandle -ne 0) {
        $el = [System.Windows.Automation.AutomationElement]::FromHandle($pr.MainWindowHandle)
        if ($el) {
            $cond = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                [System.Windows.Automation.ControlType]::Button
            )
            $btns = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
            foreach ($b in $btns) {
                $nm = [string]$b.Current.Name
                if ($nm -match '^(Connect|Disconnect|Подключ|Отключ)') {
                    $pat = $null
                    if ($b.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) {
                        $pat.Invoke()
                        break
                    }
                }
            }
        }
    }
}
"""
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                capture_output=True,
                timeout=4,
                creationflags=0x08000000,
            )
        except Exception:
            pass
        if done_cb is not None:
            try:
                done_cb()
            except Exception:
                pass

    import threading
    threading.Thread(target=_run, daemon=True).start()
    return True


def toggle_airplane_mode_async(done_cb=None, *, enable: bool | None = None) -> None:
    """Toggle wireless radios (Wi-Fi + Bluetooth) directly via WinRT without opening Settings."""
    target_literal = (
        "'Off'" if enable is True else ("'On'" if enable is False else "''")
    )
    ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -like 'IAsyncOperation*'
}})[0]
function Await($op, $type) {{
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(3500) | Out-Null
    return $t.Result
}}
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$forced = {target_literal}
if ($radios) {{
    $anyOn = $false
    foreach ($r in $radios) {{ if ($r.State -eq [Windows.Devices.Radios.RadioState]::On) {{ $anyOn = $true }} }}
    $target = if ($forced -eq 'Off') {{ [Windows.Devices.Radios.RadioState]::Off }} elseif ($forced -eq 'On') {{ [Windows.Devices.Radios.RadioState]::On }} elseif ($anyOn) {{ [Windows.Devices.Radios.RadioState]::Off }} else {{ [Windows.Devices.Radios.RadioState]::On }}
    foreach ($r in $radios) {{
        Await ($r.SetStateAsync($target)) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
    }}
}}
"""

    def _run() -> None:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                capture_output=True,
                timeout=8,
                creationflags=0x08000000,
            )
        except Exception:
            pass
        if done_cb is not None:
            try:
                done_cb()
            except Exception:
                pass

    import threading
    threading.Thread(target=_run, daemon=True).start()


def restart_graphics_displays() -> None:
    """Reset Windows display / graphics pipeline quickly via Win+Ctrl+Shift+B."""
    try:
        for vk in (VK_LWIN, 0x11, 0x10, 0x42):  # LWin, Ctrl, Shift, 'B'
            user32.keybd_event(vk, 0, 0, 0)
        for vk in (0x42, 0x10, 0x11, VK_LWIN):
            user32.keybd_event(vk, 0, 0x0002, 0)
    except Exception:
        pass


def is_airplane_mode_on() -> bool:
    """Read Windows RadioManagement SystemRadioState (1 == Airplane Mode ON)."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\RadioManagement\SystemRadioState",
        ) as key:
            val, _ = winreg.QueryValueEx(key, "")
            return int(val) == 1
    except OSError:
        return False


def toggle_radio_async(kind: str) -> None:
    """Toggle Windows Wi-Fi or Bluetooth radio cleanly via WinRT Radio API, falling back to Settings URI."""
    target = "WiFi" if kind == "wifi" else "Bluetooth"
    ps = (
        f"$ErrorActionPreference='SilentlyContinue';"
        f"Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null;"
        f"[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null;"
        f"$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -like 'IAsyncOperation*' }})[0];"
        f"$req = $asTask.MakeGenericMethod([Windows.Devices.Radios.RadioAccessStatus]).Invoke($null,@([Windows.Devices.Radios.Radio]::RequestAccessAsync())).Result;"
        f"$radios = $asTask.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]]).Invoke($null,@([Windows.Devices.Radios.Radio]::GetRadiosAsync())).Result;"
        f"$r = $radios | Where-Object {{ $_.Kind -eq '{target}' }} | Select-Object -First 1;"
        f"if ($r) {{ $next = if ($r.State -eq 'On') {{ 'Off' }} else {{ 'On' }}; $null = $r.SetStateAsync($next) }}"
        f"else {{ exit 2 }}"
    )

    def _run() -> None:
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                capture_output=True,
                timeout=6,
                creationflags=0x08000000,
            )
            if res.returncode != 0:
                os.startfile("ms-availablenetworks:" if kind == "wifi" else "ms-settings:bluetooth")  # type: ignore[attr-defined]
        except Exception:
            try:
                os.startfile("ms-availablenetworks:" if kind == "wifi" else "ms-settings:bluetooth")  # type: ignore[attr-defined]
            except Exception:
                pass

    import threading
    threading.Thread(target=_run, daemon=True).start()


def open_windows_uri(uri: str) -> None:
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
    except Exception:
        pass


def reveal_in_explorer(path: str) -> None:
    p = Path(path)
    if p.exists():
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif p.parent.exists():
        subprocess.Popen(["explorer", str(p.parent)])


def is_caps_lock_on() -> bool:
    """Return True if Windows Caps Lock toggle state is currently active."""
    try:
        return bool(user32.GetKeyState(0x14) & 0x0001)
    except Exception:
        return False


# ------------------------------------------------------------------ Global Hotkeys
_SPECIAL_VKS: dict[str, int] = {
    "SPACE": 0x20,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "PRINTSCREEN": 0x2C,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
}
for _i in range(1, 13):
    _SPECIAL_VKS[f"F{_i}"] = 0x70 + (_i - 1)


def parse_hotkey(shortcut: str) -> tuple[int, int] | None:
    """Parse a human shortcut string like 'Ctrl+Alt+Shift+V' into (modifiers, vk)."""
    if not shortcut or not shortcut.strip():
        return None
    parts = [p.strip() for p in shortcut.split("+") if p.strip()]
    mods = MOD_NOREPEAT
    vk = 0
    for part in parts:
        up = part.upper()
        if up in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif up == "ALT":
            mods |= MOD_ALT
        elif up == "SHIFT":
            mods |= MOD_SHIFT
        elif up in ("WIN", "META", "SUPER"):
            mods |= MOD_WIN
        elif up in _SPECIAL_VKS:
            vk = _SPECIAL_VKS[up]
        elif len(up) == 1 and ("A" <= up <= "Z" or "0" <= up <= "9"):
            vk = ord(up)
        else:
            return None
    if vk == 0:
        return None
    return mods, vk


class GlobalHotkeyManager(QObject, QAbstractNativeEventFilter):
    """Register Win32 global hotkeys and emit action names on trigger."""

    triggered = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._id_to_action: dict[int, str] = {}
        self._next_id = 100

    def register_all(self, bindings: dict[str, str]) -> dict[str, bool]:
        self.unregister_all()
        results: dict[str, bool] = {}
        for action, shortcut in bindings.items():
            parsed = parse_hotkey(shortcut)
            if not parsed:
                results[action] = False
                continue
            mods, vk = parsed
            hk_id = self._next_id
            self._next_id += 1
            ok = bool(user32.RegisterHotKey(None, hk_id, mods, vk))
            if ok:
                self._id_to_action[hk_id] = action
            results[action] = ok
        return results

    def unregister_all(self) -> None:
        for hk_id in list(self._id_to_action.keys()):
            user32.UnregisterHotKey(None, hk_id)
        self._id_to_action.clear()

    def nativeEventFilter(self, eventType, message):  # type: ignore[override]
        if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message == WM_HOTKEY:
                action = self._id_to_action.get(int(msg.wParam))
                if action:
                    self.triggered.emit(action)
                    return True, 0
        return False, 0


class _PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_ulong),
        ("ControlMask", ctypes.c_ulong),
        ("StateMask", ctypes.c_ulong),
    ]


_timer_period_active = False


def apply_laptop_performance_mode(remove_limits: bool = True) -> bool:
    """Disable Windows EcoQoS/PowerThrottling, boost process priority, and enable 1ms timer resolution."""
    global _timer_period_active
    ok = False
    try:
        kernel32 = ctypes.windll.kernel32
        h_proc = kernel32.GetCurrentProcess()
        # ProcessPowerThrottling = 4; ExecutionSpeed = 0x1, IgnoreTimerResolution = 0x4
        state = _PROCESS_POWER_THROTTLING_STATE(
            Version=1,
            ControlMask=0x1 | 0x4,
            StateMask=0 if remove_limits else (0x1 | 0x4),
        )
        if kernel32.SetProcessInformation(h_proc, 4, ctypes.byref(state), ctypes.sizeof(state)):
            ok = True
        prio = 0x00008000 if remove_limits else 0x00000020  # ABOVE_NORMAL_PRIORITY_CLASS vs NORMAL
        if kernel32.SetPriorityClass(h_proc, prio):
            ok = True
    except Exception:
        pass
    try:
        winmm = ctypes.windll.winmm
        if remove_limits and not _timer_period_active:
            if winmm.timeBeginPeriod(1) == 0:
                _timer_period_active = True
        elif (not remove_limits) and _timer_period_active:
            winmm.timeEndPeriod(1)
            _timer_period_active = False
    except Exception:
        pass
    return ok
