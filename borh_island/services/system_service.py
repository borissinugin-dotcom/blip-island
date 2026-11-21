"""System telemetry monitor: CPU/RAM/GPU/Disk/Net/Battery, processes, Wi-Fi/BT/VPN, privacy dot, Downloads/Steam/OBS."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..core.config import ConfigManager
from ..core.win32_api import (
    SNIPPING_OVERLAY_EXES,
    get_fast_process_table,
    get_foreground_window_info,
    is_airplane_mode_on,
    is_desktop_clean,
    list_running_process_names,
)

CREATE_NO_WINDOW = 0x08000000


@dataclass
class ProcessStat:
    pid: int
    name: str
    cpu_pct: float
    mem_mb: float


@dataclass
class BluetoothDeviceInfo:
    name: str
    connected: bool = True


@dataclass
class SystemStatsSnapshot:
    cpu_percent: float = 0.0
    cpu_per_core: list[float] = field(default_factory=list)
    cpu_freq_mhz: float = 0.0
    cpu_temp: float = -1.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    ram_temp: float = -1.0
    disk_read_mbs: float = 0.0
    disk_write_mbs: float = 0.0
    net_rx_kbs: float = 0.0
    net_tx_kbs: float = 0.0
    gpu_name: str = ""
    gpu_load: float = -1.0
    gpu_temp: float = -1.0
    gpu_vram_used_mb: float = 0.0
    gpu_vram_total_mb: float = 0.0
    battery_percent: int = -1
    battery_plugged: bool = True
    wifi_ssid: str = ""
    net_interface: str = ""
    wifi_radio_on: bool | None = None
    bt_radio_on: bool | None = None
    airplane_on: bool = False
    desktop_clean: bool = False
    vpn_active: bool = False
    vpn_name: str = ""
    vpn_country: str = ""
    vpn_ip: str = ""
    mic_in_use: bool = False
    mic_app: str = ""
    cam_in_use: bool = False
    cam_app: str = ""
    obs_status: str = ""  # "", "Recording", "Streaming"
    steam_download: str = ""  # Game name + %
    active_game_name: str = ""
    in_game: bool = False
    bluetooth_devices: list[BluetoothDeviceInfo] = field(default_factory=list)
    top_processes: list[ProcessStat] = field(default_factory=list)


def _check_privacy_device(kind: str) -> tuple[bool, str]:
    """Check Windows Registry CapabilityAccessManager ConsentStore for active mic or webcam capture."""
    root_sub = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\{kind}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_sub) as key:
            idx = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, idx)
                    idx += 1
                except OSError:
                    break
                if sub_name == "NonPackaged":
                    with winreg.OpenKey(key, "NonPackaged") as np_key:
                        j = 0
                        while True:
                            try:
                                np_name = winreg.EnumKey(np_key, j)
                                j += 1
                            except OSError:
                                break
                            with winreg.OpenKey(np_key, np_name) as app_key:
                                try:
                                    stop_val, _ = winreg.QueryValueEx(app_key, "LastUsedTimeStop")
                                    start_val, _ = winreg.QueryValueEx(app_key, "LastUsedTimeStart")
                                    if int(start_val) > 0 and int(stop_val) == 0:
                                        return True, Path(np_name.replace("#", "\\")).name
                                except OSError:
                                    pass
                else:
                    with winreg.OpenKey(key, sub_name) as app_key:
                        try:
                            stop_val, _ = winreg.QueryValueEx(app_key, "LastUsedTimeStop")
                            start_val, _ = winreg.QueryValueEx(app_key, "LastUsedTimeStart")
                            if int(start_val) > 0 and int(stop_val) == 0:
                                return True, sub_name.split("_")[0]
                        except OSError:
                            pass
    except OSError:
        pass
    return False, ""


_NVIDIA_SMI_EXE = shutil.which("nvidia-smi") or ""


class _WinFileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    def to_int(self) -> int:
        return (int(self.dwHighDateTime) << 32) | int(self.dwLowDateTime)


def _read_win32_system_times() -> tuple[int, int, int] | None:
    """Return (idle_100ns, kernel_100ns, user_100ns) from Win32 GetSystemTimes cleanly isolated per caller."""
    try:
        idle, kernel, user = _WinFileTime(), _WinFileTime(), _WinFileTime()
        if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return (idle.to_int(), kernel.to_int(), user.to_int())
    except Exception:
        pass
    return None


def _read_gpu_nvidia() -> tuple[str, float, float, float, float]:
    if not _NVIDIA_SMI_EXE:
        return ("", -1.0, -1.0, 0.0, 0.0)
    try:
        res = subprocess.run(
            [
                _NVIDIA_SMI_EXE,
                "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().splitlines()[0].split(",")]
            if len(parts) >= 5:
                return (parts[0], float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
    except Exception:
        pass
    return ("", -1.0, -1.0, 0.0, 0.0)


def _read_gpu_windows_3d_load() -> float:
    """Fallback GPU 3D engine utilization via Windows PerfCounters for AMD/Intel/NVIDIA."""
    try:
        res = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "$s=(Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples;"
                "if($s){[Math]::Round(($s|Measure-Object -Property CookedValue -Sum).Sum,1)}",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode == 0 and res.stdout.strip():
            val = float(res.stdout.strip().replace(",", "."))
            return max(0.0, min(100.0, val))
    except Exception:
        pass
    return -1.0


_NON_GAME_EXES = SNIPPING_OVERLAY_EXES | {
    "explorer.exe",
    "shellexperiencehost.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "opera.exe",
    "brave.exe",
    "browser.exe",
    "vivaldi.exe",
    "arc.exe",
    "code.exe",
    "devenv.exe",
    "pycharm64.exe",
    "idea64.exe",
    "clion64.exe",
    "rider64.exe",
    "webstorm64.exe",
    "sublime_text.exe",
    "notepad.exe",
    "notepad++.exe",
    "obsidian.exe",
    "winword.exe",
    "excel.exe",
    "powerpnt.exe",
    "spotify.exe",
    "yandexmusic.exe",
    "telegram.exe",
    "discord.exe",
    "slack.exe",
    "teams.exe",
    "ms-teams.exe",
    "zoom.exe",
    "vlc.exe",
    "mpv.exe",
    "mpc-hc64.exe",
    "potplayermini64.exe",
    "obs64.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "wt.exe",
    "windowsterminal.exe",
    "conhost.exe",
    "python.exe",
    "pythonw.exe",
    "taskmgr.exe",
    "systemsettings.exe",
    "applicationframehost.exe",
    "steam.exe",
    "steamwebhelper.exe",
    "epicgameslauncher.exe",
    "battle.net.exe",
    "riotclientservices.exe",
}

_KNOWN_GAME_EXES = {
    "cs2.exe",
    "csgo.exe",
    "dota2.exe",
    "valorant.exe",
    "valorant-win64-shipping.exe",
    "gta5.exe",
    "rdr2.exe",
    "eldenring.exe",
    "cyberpunk2077.exe",
    "witcher3.exe",
    "minecraft.exe",
    "robloxplayerbeta.exe",
    "fortniteclient-win64-shipping.exe",
    "r5apex.exe",
    "overwatch.exe",
    "tslgame.exe",
    "rustclient.exe",
    "terraria.exe",
    "bg3.exe",
    "bg3_dx11.exe",
    "helldivers2.exe",
    "palworld-win64-shipping.exe",
    "deadlock.exe",
    "escapefromtarkov.exe",
    "worldoftanks.exe",
    "aces.exe",
    "genshinimpact.exe",
    "starrail.exe",
    "zenlesszonezero.exe",
    "eurotrucks2.exe",
    "amtrucks.exe",
    "beamng.drive.x64.exe",
    "hl2.exe",
    "tf_win64.exe",
    "left4dead2.exe",
    "hollow_knight.exe",
    "hades.exe",
    "hades2.exe",
    "subnautica.exe",
    "stardew valley.exe",
    "factorio.exe",
    "rimworldwin64.exe",
    "pathofexile_x64.exe",
    "warframe.x64.exe",
    "destiny2.exe",
}

_GAME_DIR_HINTS = (
    "\\steamapps\\common\\",
    "\\epic games\\",
    "\\riot games\\",
    "\\gog galaxy\\games\\",
    "\\xboxgames\\",
    "\\ubisoft game launcher\\games\\",
    "\\ea games\\",
    "\\battle.net\\",
    "\\games\\",
)


def is_game_process(exe_name: str, exe_path: str = "", title: str = "", is_fullscreen: bool = False) -> bool:
    """Return True if a foreground window represents a running game (excluding regular desktop apps)."""
    low_exe = (exe_name or "").strip().lower()
    if not low_exe or low_exe in _NON_GAME_EXES:
        return False
    if low_exe in _KNOWN_GAME_EXES:
        return True
    if low_exe == "javaw.exe" and ("minecraft" in (title or "").lower() or "tlauncher" in (title or "").lower()):
        return True
    low_path = (exe_path or "").lower().replace("/", "\\")
    if low_path and any(h in low_path for h in _GAME_DIR_HINTS):
        return True
    if "-win64-shipping.exe" in low_exe or "win64-shipping" in low_exe:
        return True
    if is_fullscreen:
        return True
    return False


_PID_EXE_CACHE: dict[int, str] = {}


def detect_active_game() -> tuple[bool, str]:
    """Inspect current foreground window and detect whether a game (not a regular app) is active."""
    fg = get_foreground_window_info()
    if fg is None or fg.pid == os.getpid():
        return False, ""
    low_exe = (fg.exe_name or "").strip().lower()
    if not low_exe or low_exe in _NON_GAME_EXES:
        return False, ""
    if low_exe in _KNOWN_GAME_EXES or "-win64-shipping.exe" in low_exe or "win64-shipping" in low_exe or fg.is_fullscreen:
        display = (fg.title or fg.exe_name.replace(".exe", "")).strip()
        return True, display[:32]
    exe_path = _PID_EXE_CACHE.get(fg.pid)
    if exe_path is None:
        try:
            exe_path = psutil.Process(fg.pid).exe()
        except Exception:
            exe_path = ""
        if len(_PID_EXE_CACHE) > 256:
            _PID_EXE_CACHE.clear()
        _PID_EXE_CACHE[fg.pid] = exe_path
    if is_game_process(fg.exe_name, exe_path=exe_path, title=fg.title, is_fullscreen=fg.is_fullscreen):
        display = (fg.title or fg.exe_name.replace(".exe", "")).strip()
        return True, display[:32]
    return False, ""


def _read_wifi_state() -> tuple[str, bool | None]:
    """Return (connected_ssid, software_radio_on_or_none) from netsh wlan."""
    ssid = ""
    radio_on: bool | None = None
    try:
        res = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            timeout=2.0,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in res.stdout.splitlines():
            m = re.match(r"^\s*SSID\s*:\s*(.+)$", line)
            if m:
                val = m.group(1).strip()
                if val:
                    ssid = val
                    radio_on = True
                continue
            low = line.lower()
            if "software off" in low or "программно откл" in low or "отключен" in low:
                if "radio" in low or "радио" in low or "software" in low or "программ" in low or ":" in low:
                    if radio_on is None:
                        radio_on = False
            elif "software on" in low or "программно вкл" in low:
                radio_on = True
    except Exception:
        pass
    return ssid, radio_on


def _read_wifi_ssid() -> str:
    ssid, _ = _read_wifi_state()
    return ssid


_VPN_TOKEN_RE = re.compile(r"(?<![a-z0-9])(utun\d*|tun\d*|tap\d*|wg\d*|vpn)(?![a-z0-9])")


def _detect_vpn_interface() -> tuple[bool, str]:
    excluded = (
        "teredo",
        "isatap",
        "6to4",
        "iphttps",
        "ip-https",
        "pseudo-interface",
        "псевдоинтерфейс",
        "wan miniport",
        "kernel debug",
        "wi-fi direct",
        "radmin",
        "hamachi",
        "zerotier",
        "vmware",
        "virtualbox",
        "hyper-v",
        "vethernet",
        "loopback",
    )
    keywords = (
        "amnezia",
        "amneziawg",
        "wireguard",
        "wintun",
        "openvpn",
        "nekoray",
        "sing-box",
        "nordlynx",
        "mullvad",
        "warp",
        "vless",
        "xray",
        "outline",
        "proton",
        "tunnelbear",
        "windscribe",
    )
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    for name, st in stats.items():
        if not st.isup:
            continue
        low = name.lower()
        if any(ex in low for ex in excluded):
            continue
        if any(k in low for k in keywords) or _VPN_TOKEN_RE.search(low):
            # Ensure it has a non-link-local IPv4 or IPv6 (excluding Windows Teredo 2001:0: prefix)
            for addr in addrs.get(name, []):
                ip_s = str(addr.address).lower()
                if addr.family == socket.AF_INET and not ip_s.startswith("169.254."):
                    return True, name
                if addr.family == socket.AF_INET6 and not ip_s.startswith(("fe80:", "2001:0:")):
                    return True, name
    return False, ""


def _scan_steam_downloading() -> str:
    roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam" / "steamapps",
        Path(r"C:\SteamLibrary\steamapps"),
        Path(r"D:\SteamLibrary\steamapps"),
        Path(r"E:\SteamLibrary\steamapps"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for acf in root.glob("appmanifest_*.acf"):
            try:
                txt = acf.read_text(encoding="utf-8", errors="ignore")
                flags_m = re.search(r'"StateFlags"\s+"(\d+)"', txt)
                if not flags_m:
                    continue
                flags = int(flags_m.group(1))
                # Bit 1024 / 2048 / 2 usually indicates active update/downloading on Steam (4 == FullyInstalled)
                if flags != 4 and (flags & (2 | 1024 | 2048)):
                    name_m = re.search(r'"name"\s+"([^"]+)"', txt)
                    dl_m = re.search(r'"BytesDownloaded"\s+"(\d+)"', txt)
                    tot_m = re.search(r'"BytesToDownload"\s+"(\d+)"', txt)
                    gname = name_m.group(1) if name_m else acf.stem
                    if dl_m and tot_m and int(tot_m.group(1)) > 0:
                        pct = int(round(int(dl_m.group(1)) * 100.0 / int(tot_m.group(1))))
                        return f"{gname} ({pct}%)"
                    return gname
            except Exception:
                continue
    return ""


def _detect_obs_status(procs: set[str] | None = None) -> str:
    pnames = procs if procs is not None else list_running_process_names()
    obs_running = ("obs64.exe" in pnames) or ("obs32.exe" in pnames)
    if not obs_running:
        return ""
    logs_dir = Path(os.environ.get("APPDATA", "")) / "obs-studio" / "logs"
    if logs_dir.exists():
        try:
            latest = max(logs_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime)
            tail = latest.read_text(encoding="utf-8", errors="ignore")[-4000:]
            rec_on = tail.rfind("==== Recording Start") > tail.rfind("==== Recording Stop")
            str_on = tail.rfind("==== Streaming Start") > tail.rfind("==== Streaming Stop")
            if rec_on and str_on:
                return "Live + Rec"
            if str_on:
                return "Streaming"
            if rec_on:
                return "Recording"
        except Exception:
            pass
    return "Standby"


class SystemMonitorService(QObject):
    """Collects telemetry, privacy dots, VPN, Downloads, Steam, and OBS alerts without stalling UI."""

    stats_updated = pyqtSignal(object)  # SystemStatsSnapshot
    vpn_changed = pyqtSignal(bool, str, str, str)  # active, iface, country, ip
    download_started = pyqtSignal(str)  # filename
    download_completed = pyqtSignal(str)  # full path
    notification_received = pyqtSignal(str, str, str)  # app, title, body

    def __init__(self, config_mgr: ConfigManager, winrt_worker=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config_mgr = config_mgr
        self._winrt_worker = winrt_worker
        self._last_disk = psutil.disk_io_counters()
        self._last_net = psutil.net_io_counters()
        self._init_t = time.monotonic()
        self._last_t = self._init_t
        self._busy = False
        self._last_vpn_state: bool | None = None
        self._vpn_country = ""
        self._vpn_ip = ""
        self._bt_devices: list[BluetoothDeviceInfo] = []
        self._bt_last_scan = -100.0
        self._bt_busy = False
        self._cached_wifi_radio: bool | None = None
        self._cached_bt_radio: bool | None = None
        self._cached_wifi: str = ""
        self._win_gpu_val = -1.0
        self._win_gpu_busy = False
        self._known_downloads: set[str] = self._snapshot_downloads()
        self._active_dl_temps: set[str] = set()
        self._tick_count = 0
        self._prev_sys_times = _read_win32_system_times()
        self._last_good_cpu_pct = 0.0
        self._prev_proc_times: dict[int, int] = {}
        self._prev_proc_ts = 0.0
        self._last_dl_mtime: float = -1.0
        self._temps_miss_streak = 0
        self._demand_telemetry = False

        # Prime CPU counters once without percpu spike
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

        self._timer = QTimer(self)
        self._timer.setInterval(2400)
        self._timer.timeout.connect(self.poll_now)

    def set_telemetry_demand(self, active: bool) -> None:
        """Signal when expanded UI or telemetry consumers need fresher GPU/process/sensor updates."""
        self._demand_telemetry = bool(active)

    def start(self) -> None:
        self._timer.start()
        self.poll_now()

    def stop(self) -> None:
        self._timer.stop()

    @staticmethod
    def _snapshot_downloads() -> set[str]:
        dl = Path.home() / "Downloads"
        if not dl.exists():
            return set()
        try:
            with os.scandir(dl) as it:
                return {e.name for e in it if e.is_file(follow_symlinks=False)}
        except OSError:
            return set()

    def poll_now(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._tick_count += 1
        tick = self._tick_count
        threading.Thread(target=lambda: self._collect(tick), daemon=True).start()

    def _collect(self, tick: int) -> None:
        try:
            if tick == 1:
                elapsed = time.monotonic() - self._init_t
                if elapsed < 0.12:
                    time.sleep(min(0.12, 0.12 - elapsed))
            now_t = time.monotonic()
            dt = max(0.2, now_t - self._last_t)
            self._last_t = now_t

            win_cpu: float | None = None
            cur_sys = _read_win32_system_times()
            if cur_sys is not None and self._prev_sys_times is not None:
                d_idle = cur_sys[0] - self._prev_sys_times[0]
                d_kern = cur_sys[1] - self._prev_sys_times[1]
                d_user = cur_sys[2] - self._prev_sys_times[2]
                d_tot = d_kern + d_user
                if d_tot > 0:
                    win_cpu = max(0.0, min(100.0, ((d_tot - d_idle) / d_tot) * 100.0))
            if cur_sys is not None:
                self._prev_sys_times = cur_sys

            ps_tot = psutil.cpu_percent(interval=None)
            cpu_cores = psutil.cpu_percent(interval=None, percpu=True)
            if win_cpu is not None and (win_cpu > 0.05 or ps_tot <= 0.05):
                cpu_tot = win_cpu
            elif ps_tot > 0.05:
                cpu_tot = ps_tot
            elif cpu_cores and any(c > 0.05 for c in cpu_cores):
                cpu_tot = sum(cpu_cores) / max(1, len(cpu_cores))
            else:
                cpu_tot = self._last_good_cpu_pct if (now_t - self._init_t) > 0.25 else 0.0
            self._last_good_cpu_pct = cpu_tot

            freq = psutil.cpu_freq()
            vm = psutil.virtual_memory()

            d_io = psutil.disk_io_counters()
            d_read = d_write = 0.0
            if d_io and self._last_disk:
                d_read = max(0.0, (d_io.read_bytes - self._last_disk.read_bytes) / (1024 * 1024 * dt))
                d_write = max(0.0, (d_io.write_bytes - self._last_disk.write_bytes) / (1024 * 1024 * dt))
            self._last_disk = d_io

            n_io = psutil.net_io_counters()
            n_rx = n_tx = 0.0
            if n_io and self._last_net:
                n_rx = max(0.0, (n_io.bytes_recv - self._last_net.bytes_recv) / (1024 * dt))
                n_tx = max(0.0, (n_io.bytes_sent - self._last_net.bytes_sent) / (1024 * dt))
            self._last_net = n_io

            bat = psutil.sensors_battery()
            bat_pct = int(round(bat.percent)) if bat else -1
            bat_plug = bool(bat.power_plugged) if bat else True

            in_game, game_name = detect_active_game()
            demand = in_game or getattr(self, "_demand_telemetry", False)
            procs_set = list_running_process_names()
            # Poll GPU smoothly: fast when in game or panel expanded, calm (~19s) when collapsed desktop idle to avoid nvidia-smi microstutters
            gpu_cached: tuple[str, float, float, float, float] = getattr(
                self, "_cached_gpu", ("", -1.0, -1.0, 0.0, 0.0)
            )
            want_gpu = (tick == 1) or (tick % 2 == 1 if demand else tick % 8 == 1)
            if want_gpu:
                gpu_name, gpu_load, gpu_temp, gpu_vram_u, gpu_vram_t = _read_gpu_nvidia()
                if gpu_load < 0:
                    if (in_game and tick % 3 == 1) or tick % 10 == 1:
                        self._trigger_win_gpu_bg()
                    if self._win_gpu_val >= 0:
                        gpu_load = self._win_gpu_val
                        if not gpu_name:
                            gpu_name = "GPU"
                    elif gpu_cached[1] >= 0:
                        gpu_name, gpu_load, gpu_temp, gpu_vram_u, gpu_vram_t = gpu_cached
                self._cached_gpu = (gpu_name, gpu_load, gpu_temp, gpu_vram_u, gpu_vram_t)
            else:
                gpu_name, gpu_load, gpu_temp, gpu_vram_u, gpu_vram_t = gpu_cached

            mic_on, mic_app = _check_privacy_device("microphone")
            cam_on, cam_app = _check_privacy_device("webcam")

            vpn_on, vpn_name = _detect_vpn_interface()
            if vpn_on != self._last_vpn_state:
                self._last_vpn_state = vpn_on
                if self.config_mgr.config.vpn_ip_lookup:
                    self._lookup_ip_bg(vpn_on, vpn_name)
                else:
                    self._vpn_country, self._vpn_ip = "", ""
                    self.vpn_changed.emit(vpn_on, vpn_name, "", "")

            # Refresh netsh Wi-Fi + radio states cleanly without spawning netsh every 4.8s when collapsed idle
            radio_cadence = 3 if demand else 6
            if tick == 1 or tick % radio_cadence == 1 or getattr(self, "_force_radio_refresh", False):
                self._force_radio_refresh = False
                wifi, w_rad = _read_wifi_state()
                self._cached_wifi = wifi
                if self._winrt_worker is not None:
                    try:
                        rad = self._winrt_worker.get_radio_states()
                        if rad.get("ok"):
                            if rad.get("wifi") is not None:
                                self._cached_wifi_radio = bool(rad.get("wifi"))
                            elif w_rad is not None:
                                self._cached_wifi_radio = w_rad
                            if rad.get("bt") is not None:
                                self._cached_bt_radio = bool(rad.get("bt"))
                    except Exception:
                        if w_rad is not None:
                            self._cached_wifi_radio = w_rad
                elif w_rad is not None:
                    self._cached_wifi_radio = w_rad
            else:
                wifi = getattr(self, "_cached_wifi", "")
            wifi_rad = self._cached_wifi_radio
            bt_rad = self._cached_bt_radio
            air_on = is_airplane_mode_on()
            desk_clean = is_desktop_clean()
            steam_dl = (
                _scan_steam_downloading()
                if (self.config_mgr.config.steam_downloads_enabled and "steam.exe" in procs_set and tick % 5 == 1)
                else getattr(self, "_cached_steam", "")
            )
            self._cached_steam = steam_dl
            obs = (
                _detect_obs_status(procs_set)
                if (self.config_mgr.config.obs_status_enabled and tick % 2 == 1)
                else getattr(self, "_cached_obs", "")
            )
            self._cached_obs = obs

            # Fast zero-OpenProcess process table (~5ms via NtQuerySystemInformation(5)) every ~14s (or ~7s when panel open)
            top_procs: list[ProcessStat] = getattr(self, "_cached_procs", [])
            proc_cadence = 3 if demand else 6
            if tick >= 2 and tick % proc_cadence == 2:
                fast_rows = get_fast_process_table()
                n_cpus = max(1, psutil.cpu_count() or 1)
                now_pt = time.monotonic()
                dt_pt = max(0.5, now_pt - getattr(self, "_prev_proc_ts", 0.0)) if getattr(self, "_prev_proc_ts", 0.0) > 0 else 0.0
                if fast_rows is not None:
                    prev_map = getattr(self, "_prev_proc_times", {})
                    new_map: dict[int, int] = {}
                    plist: list[ProcessStat] = []
                    for pid, pname, cpu_100ns, ws_mb in fast_rows:
                        new_map[pid] = cpu_100ns
                        cpu_pct = 0.0
                        if dt_pt > 0.0 and pid in prev_map:
                            dc = max(0, cpu_100ns - prev_map[pid])
                            cpu_pct = min(100.0, (dc / (10_000_000.0 * dt_pt * n_cpus)) * 100.0)
                        plist.append(ProcessStat(pid=pid, name=pname, cpu_pct=cpu_pct, mem_mb=ws_mb))
                    self._prev_proc_times = new_map
                    self._prev_proc_ts = now_pt
                    plist.sort(key=lambda x: (x.cpu_pct, x.mem_mb), reverse=True)
                    top_procs = plist[:6]
                else:
                    plist = []
                    for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
                        try:
                            info = p.info
                            if (info.get("pid") or 0) <= 4:
                                continue
                            cpu = (info.get("cpu_percent") or 0.0) / n_cpus
                            plist.append(ProcessStat(pid=info["pid"], name=info.get("name") or "?", cpu_pct=cpu, mem_mb=0.0))
                        except Exception:
                            continue
                    plist.sort(key=lambda x: x.cpu_pct, reverse=True)
                    top_procs = plist[:6]
                self._cached_procs = top_procs

            # Non-blocking Bluetooth scan every 20s (or 15s when expanded) so setupapi never runs unnecessarily
            bt_interval = 15.0 if demand else 24.0
            if time.monotonic() - self._bt_last_scan > bt_interval:
                self._bt_last_scan = time.monotonic()
                self._trigger_bt_bg()

            # Watch Downloads via directory mtime + os.scandir (zero per-file stat syscalls when unchanged)
            dl_dir = Path.home() / "Downloads"
            if dl_dir.exists():
                try:
                    dl_mtime = dl_dir.stat().st_mtime
                except OSError:
                    dl_mtime = -1.0
                if dl_mtime != self._last_dl_mtime or bool(self._active_dl_temps):
                    self._last_dl_mtime = dl_mtime
                    try:
                        with os.scandir(dl_dir) as it:
                            cur_files = {e.name for e in it if e.is_file(follow_symlinks=False)}
                    except OSError:
                        cur_files = self._known_downloads
                    added = cur_files - self._known_downloads
                    self._known_downloads = cur_files
                    temp_exts = (".crdownload", ".part", ".tmp", ".opdownload")
                    cur_temps = {f for f in cur_files if f.lower().endswith(temp_exts)}
                    new_temps = cur_temps - self._active_dl_temps
                    self._active_dl_temps = cur_temps
                    for tname in new_temps:
                        clean_name = tname
                        for ext in temp_exts:
                            if clean_name.lower().endswith(ext):
                                clean_name = clean_name[: -len(ext)] or tname
                                break
                        self.download_started.emit(clean_name)
                    for fname in added:
                        low = fname.lower()
                        if not low.endswith(temp_exts):
                            self.download_completed.emit(str(dl_dir / fname))

            if self._winrt_worker is not None and getattr(self.config_mgr.config, "show_system_notifications", True):
                try:
                    excl = {
                        x.strip().lower()
                        for x in (getattr(self.config_mgr.config, "notification_excluded_apps", []) or [])
                        if x.strip()
                    }
                    for nitem in self._winrt_worker.get_notifications():
                        app_name = str(nitem.get("app") or "").strip()
                        app_id = str(nitem.get("app_id") or "").strip()
                        if excl and (app_name.lower() in excl or app_id.lower() in excl):
                            continue
                        ntitle = str(nitem.get("title") or "").strip()
                        nbody = str(nitem.get("body") or "").strip()
                        if ntitle or nbody:
                            self.notification_received.emit(app_name or "Уведомление", ntitle, nbody)
                except Exception:
                    pass

            has_sensor_app = bool(procs_set & {"librehardwaremonitor.exe", "openhardwaremonitor.exe", "hwinfo64.exe"})
            temp_cadence = 4 if (has_sensor_app or demand) else (50 if self._temps_miss_streak >= 2 else 10)
            if tick == 1 or tick % temp_cadence == 1:
                self._trigger_cpu_ram_temps_bg()
            cpu_temp, ram_temp = getattr(self, "_cached_cpu_ram_temps", (-1.0, -1.0))

            snap = SystemStatsSnapshot(
                cpu_percent=cpu_tot,
                cpu_per_core=cpu_cores,
                cpu_freq_mhz=freq.current if freq else 0.0,
                cpu_temp=cpu_temp,
                ram_percent=vm.percent,
                ram_used_gb=vm.used / (1024 ** 3),
                ram_total_gb=vm.total / (1024 ** 3),
                ram_temp=ram_temp,
                disk_read_mbs=d_read,
                disk_write_mbs=d_write,
                net_rx_kbs=n_rx,
                net_tx_kbs=n_tx,
                gpu_name=gpu_name,
                gpu_load=gpu_load,
                gpu_temp=gpu_temp,
                gpu_vram_used_mb=gpu_vram_u,
                gpu_vram_total_mb=gpu_vram_t,
                battery_percent=bat_pct,
                battery_plugged=bat_plug,
                wifi_ssid=wifi,
                net_interface="Wi-Fi" if wifi else ("Ethernet" if n_rx + n_tx >= 0 else ""),
                wifi_radio_on=wifi_rad,
                bt_radio_on=bt_rad,
                airplane_on=air_on,
                desktop_clean=desk_clean,
                vpn_active=vpn_on,
                vpn_name=vpn_name,
                vpn_country=self._vpn_country,
                vpn_ip=self._vpn_ip,
                mic_in_use=mic_on,
                mic_app=mic_app,
                cam_in_use=cam_on,
                cam_app=cam_app,
                obs_status=obs,
                steam_download=steam_dl,
                active_game_name=game_name,
                in_game=in_game,
                bluetooth_devices=self._bt_devices,
                top_processes=top_procs,
            )
            self.stats_updated.emit(snap)
        finally:
            self._busy = False

    def _trigger_cpu_ram_temps_bg(self) -> None:
        if getattr(self, "_temps_busy", False):
            return
        self._temps_busy = True

        def _work() -> None:
            cpu_t = -1.0
            ram_t = -1.0
            try:
                if sys.platform == "win32":
                    ps = (
                        "$ErrorActionPreference='SilentlyContinue';"
                        "$cpu=-1;$ram=-1;"
                        "foreach($ns in 'root\\LibreHardwareMonitor','root\\OpenHardwareMonitor'){"
                        "$s=Get-CimInstance -Namespace $ns -ClassName Sensor | Where-Object {$_.SensorType -eq 'Temperature'};"
                        "if($s){"
                        "$c=($s | Where-Object {$_.Name -match 'CPU Package|Core Max|Tctl|Package'} | Select-Object -First 1).Value;"
                        "if(-not $c){$c=($s | Where-Object {$_.Parent -match 'cpu' -or $_.Name -match 'CPU'} | Select-Object -First 1).Value};"
                        "if($c -gt 0 -and $c -lt 130){$cpu=[double]$c};"
                        "$r=($s | Where-Object {$_.Parent -match 'ram|memory' -or $_.Name -match 'DIMM|Memory|RAM'} | Select-Object -First 1).Value;"
                        "if($r -gt 0 -and $r -lt 120){$ram=[double]$r};"
                        "if($cpu -gt 0){break}"
                        "}};"
                        "if($cpu -le 0){"
                        "$tz=Get-CimInstance -Namespace root\\wmi -ClassName MSAcpi_ThermalZoneTemperature | Select-Object -First 1;"
                        "if($tz -and $tz.CurrentTemperature){$t=([double]$tz.CurrentTemperature/10.0)-273.15;if($t -gt 10 -and $t -lt 125){$cpu=$t}}"
                        "};"
                        "if($cpu -le 0){"
                        "$pc=Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation | Select-Object -First 1;"
                        "if($pc -and $pc.Temperature){$t=[double]$pc.Temperature-273.15;if($t -gt 10 -and $t -lt 125){$cpu=$t}}"
                        "};"
                        "Write-Output \"$cpu|$ram\""
                    )
                    res = subprocess.run(
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                        capture_output=True,
                        text=True,
                        timeout=2.5,
                        creationflags=0x08000000,
                    )
                    out = (res.stdout or "").strip().splitlines()
                    if out and "|" in out[-1]:
                        parts = out[-1].split("|", 1)
                        try:
                            c_val = float(parts[0].replace(",", "."))
                            if 10.0 < c_val < 130.0:
                                cpu_t = c_val
                        except Exception:
                            pass
                        try:
                            r_val = float(parts[1].replace(",", "."))
                            if 10.0 < r_val < 120.0:
                                ram_t = r_val
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                self._cached_cpu_ram_temps = (cpu_t, ram_t)
                if cpu_t <= 0 and ram_t <= 0:
                    self._temps_miss_streak = getattr(self, "_temps_miss_streak", 0) + 1
                else:
                    self._temps_miss_streak = 0
                self._temps_busy = False

        threading.Thread(target=_work, daemon=True).start()

    def _trigger_win_gpu_bg(self) -> None:
        if self._win_gpu_busy:
            return
        self._win_gpu_busy = True

        def _run() -> None:
            try:
                val = _read_gpu_windows_3d_load()
                if val >= 0:
                    self._win_gpu_val = val
            finally:
                self._win_gpu_busy = False

        threading.Thread(target=_run, daemon=True).start()

    def _trigger_bt_bg(self) -> None:
        if self._bt_busy:
            return
        self._bt_busy = True

        def _run() -> None:
            try:
                devs = self._scan_bluetooth_devices()
                self._bt_devices = devs
            finally:
                self._bt_busy = False

        threading.Thread(target=_run, daemon=True).start()

    def _lookup_ip_bg(self, vpn_on: bool, vpn_name: str) -> None:
        def _run() -> None:
            country, ip = "", ""
            try:
                with httpx.Client(timeout=5.0) as client:
                    r = client.get("https://ipwho.is/")
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("success", True):
                            country = str(data.get("country_code") or data.get("country") or "")
                            ip = str(data.get("ip") or "")
            except Exception:
                pass
            self._vpn_country = country
            self._vpn_ip = ip
            self.vpn_changed.emit(vpn_on, vpn_name, country, ip)

        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def _scan_bluetooth_devices() -> list[BluetoothDeviceInfo]:
        if os.name != "nt":
            return []
        try:
            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

            class _SP_DEVINFO_DATA(ctypes.Structure):
                _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", _GUID), ("DevInst", wt.DWORD), ("Reserved", ctypes.c_void_p)]

            class _DEVPROPKEY(ctypes.Structure):
                _fields_ = [("fmtid", _GUID), ("pid", wt.ULONG)]

            bt_cls = _GUID(0xE0CBF06C, 0xCD8B, 0x4647, (ctypes.c_ubyte * 8)(0xBB, 0x8A, 0x26, 0x3B, 0x43, 0xF0, 0xF9, 0x74))
            key_name = _DEVPROPKEY(_GUID(0xB725F130, 0x47EF, 0x101A, (ctypes.c_ubyte * 8)(0xA5, 0xF1, 0x02, 0x60, 0x8C, 0x9E, 0xEB, 0xAC)), 10)
            key_friendly = _DEVPROPKEY(_GUID(0xA45C254E, 0xDF1C, 0x4EFD, (ctypes.c_ubyte * 8)(0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0)), 14)
            key_connected = _DEVPROPKEY(_GUID(0x83DA6326, 0x97A6, 0x4088, (ctypes.c_ubyte * 8)(0x94, 0x53, 0xA1, 0x92, 0x3F, 0x57, 0x3B, 0x29)), 15)

            setup = ctypes.windll.setupapi
            setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(_GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
            setup.SetupDiGetClassDevsW.restype = ctypes.c_void_p
            setup.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_SP_DEVINFO_DATA)]
            setup.SetupDiEnumDeviceInfo.restype = wt.BOOL
            setup.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), wt.LPWSTR, wt.DWORD, ctypes.POINTER(wt.DWORD)]
            setup.SetupDiGetDeviceInstanceIdW.restype = wt.BOOL
            setup.SetupDiGetDevicePropertyW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SP_DEVINFO_DATA), ctypes.POINTER(_DEVPROPKEY), ctypes.POINTER(wt.ULONG), ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
            setup.SetupDiGetDevicePropertyW.restype = wt.BOOL
            setup.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
            setup.SetupDiDestroyDeviceInfoList.restype = wt.BOOL

            DIGCF_PRESENT = 0x02
            hdev = setup.SetupDiGetClassDevsW(ctypes.byref(bt_cls), None, None, DIGCF_PRESENT)
            if not hdev or hdev == ctypes.c_void_p(-1).value:
                return []
            by_name: dict[str, bool] = {}
            order: list[str] = []
            try:
                idx = 0
                dev = _SP_DEVINFO_DATA()
                dev.cbSize = ctypes.sizeof(_SP_DEVINFO_DATA)
                id_buf = ctypes.create_unicode_buffer(512)
                name_buf = ctypes.create_unicode_buffer(512)
                prop_type = wt.ULONG()
                req = wt.DWORD()
                conn_val = ctypes.c_ubyte(0)
                while setup.SetupDiEnumDeviceInfo(hdev, idx, ctypes.byref(dev)):
                    idx += 1
                    if not setup.SetupDiGetDeviceInstanceIdW(hdev, ctypes.byref(dev), id_buf, 512, ctypes.byref(req)):
                        continue
                    up = id_buf.value.upper()
                    # Only actual paired Classic/BLE end-devices, excluding Windows BT profile services (BTHENUM\{GUID}...).
                    if not (up.startswith("BTHENUM\\DEV_") or up.startswith("BTHLE\\DEV_")):
                        continue
                    name = ""
                    if setup.SetupDiGetDevicePropertyW(hdev, ctypes.byref(dev), ctypes.byref(key_name), ctypes.byref(prop_type), name_buf, ctypes.sizeof(name_buf), ctypes.byref(req), 0):
                        name = name_buf.value.strip()
                    if not name and setup.SetupDiGetDevicePropertyW(hdev, ctypes.byref(dev), ctypes.byref(key_friendly), ctypes.byref(prop_type), name_buf, ctypes.sizeof(name_buf), ctypes.byref(req), 0):
                        name = name_buf.value.strip()
                    if not name or re.search(r"Adapter|Radio|Enumerator|Transport|Generic Bluetooth", name, re.I):
                        continue
                    conn_val.value = 0
                    is_conn = bool(
                        setup.SetupDiGetDevicePropertyW(hdev, ctypes.byref(dev), ctypes.byref(key_connected), ctypes.byref(prop_type), ctypes.byref(conn_val), 1, ctypes.byref(req), 0)
                        and conn_val.value != 0
                    )
                    if name not in by_name:
                        by_name[name] = is_conn
                        order.append(name)
                    else:
                        by_name[name] = by_name[name] or is_conn
            finally:
                setup.SetupDiDestroyDeviceInfoList(hdev)
            out = [BluetoothDeviceInfo(name=s, connected=by_name[s]) for s in order]
            out.sort(key=lambda d: (not d.connected, d.name.casefold()))
            return out[:12]
        except Exception:
            return []
