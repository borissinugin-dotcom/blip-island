"""Discord Rich Presence IPC service via Windows Named Pipes (no external dependencies)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import threading
import time
import uuid

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

OP_HANDSHAKE = 0
OP_FRAME = 1
OP_CLOSE = 2
OP_PING = 3
OP_PONG = 4

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


if sys.platform == "win32":
    _k32 = ctypes.windll.kernel32
    _k32.CreateFileW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
        wt.HANDLE,
    ]
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.WriteFile.argtypes = [
        wt.HANDLE,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
        ctypes.c_void_p,
    ]
    _k32.WriteFile.restype = wt.BOOL
    _k32.ReadFile.argtypes = [
        wt.HANDLE,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
        ctypes.c_void_p,
    ]
    _k32.ReadFile.restype = wt.BOOL
    _k32.PeekNamedPipe.argtypes = [
        wt.HANDLE,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
        ctypes.POINTER(wt.DWORD),
        ctypes.POINTER(wt.DWORD),
    ]
    _k32.PeekNamedPipe.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL


def pack_ipc_frame(opcode: int, payload: dict) -> bytes:
    """Encode a Discord IPC binary frame: <II little-endian header + UTF-8 JSON body."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("<II", int(opcode), len(body)) + body


def unpack_ipc_frame(raw: bytes) -> tuple[int, dict]:
    """Decode a Discord IPC frame header + JSON payload."""
    if len(raw) < 8:
        raise ValueError("Truncated Discord IPC header")
    opcode, length = struct.unpack("<II", raw[:8])
    body = raw[8 : 8 + length]
    data = json.loads(body.decode("utf-8", errors="replace")) if body else {}
    return opcode, data if isinstance(data, dict) else {}


def find_discord_ipc_pipe() -> str | None:
    """Return the first active \\\\.\\pipe\\discord-ipc-N path if Discord Desktop is running."""
    if sys.platform != "win32":
        return None
    try:
        names = set(os.listdir(r"\\.\pipe"))
    except Exception:
        return None
    for idx in range(10):
        cand = f"discord-ipc-{idx}"
        if cand in names:
            return rf"\\.\pipe\{cand}"
    return None


def is_discord_running() -> bool:
    """Check if Discord IPC pipe or Discord process is running (zero OpenProcess overhead)."""
    if find_discord_ipc_pipe() is not None:
        return True
    try:
        from ..core.win32_api import list_running_process_names

        procs = list_running_process_names()
        return ("discord.exe" in procs) or ("discordptb.exe" in procs) or ("discordcanary.exe" in procs)
    except Exception:
        pass
    return False


class DiscordPresenceService(QObject):
    """Background Rich Presence publisher over Discord's local IPC named pipe."""

    status_changed = pyqtSignal(bool, str)  # (connected, status_description)
    voice_state_changed = pyqtSignal(bool, bool, bool)  # (in_voice, muted, deafened)
    running_changed = pyqtSignal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._enabled = True
        self._client_id = ""
        self._pipe_handle: int | None = None
        self._connected = False
        self._status_text = "Idle"
        self._start_ts = int(time.time())
        self._lock = threading.Lock()
        self._syncing = False
        self._last_running: bool | None = None

        self._details = "Dynamic Island — Borh-edition"
        self._state = "Рабочий стол • Active"
        self._small_text = "Borh-edition"
        self._last_sent_sig = ""
        self._muted = False
        self._deafened = False
        self._in_voice = False
        self._last_sent_t = 0.0
        self._bridge = None
        self._config_mgr = None

        self._timer = QTimer(self)
        self._timer.setInterval(4500)
        self._timer.timeout.connect(self._tick)

    def attach_audio_bridge(self, bridge, config_mgr=None) -> None:
        """Attach NativeBridge and ConfigManager so Mute/Deafen work directly via WASAPI too."""
        self._bridge = bridge
        self._config_mgr = config_mgr

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def status_text(self) -> str:
        return self._status_text

    def configure(self, enabled: bool, client_id: str) -> None:
        cleaned = (client_id or "").strip()
        reconfigure = (enabled != self._enabled) or (cleaned != self._client_id)
        self._enabled = bool(enabled)
        self._client_id = cleaned
        if reconfigure:
            self._close_pipe_async()
            self._last_sent_sig = ""
            if self._timer.isActive():
                self._tick()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self._tick()

    def stop(self) -> None:
        self._timer.stop()
        self._close_pipe_sync(clear_activity=True)

    def update_from_island(
        self,
        *,
        playing: bool = False,
        title: str = "",
        artist: str = "",
        source: str = "",
        in_game: bool = False,
        game_name: str = "",
        cpu_pct: float = 0.0,
        gpu_pct: float = 0.0,
        recording: bool = False,
        timer_text: str = "",
        is_day: bool = True,
    ) -> None:
        """Build human-readable Rich Presence text from current Dynamic Island state."""
        if recording:
            details = "Запись экрана"
            state = "Dynamic Island — Borh-edition"
        elif playing and (title or artist):
            track = f"{artist} — {title}".strip(" —") if artist else title
            details = f"🎵 {track[:110]}"
            src = source or "Music"
            state = f"{src} • Dynamic Island"
        elif in_game and game_name:
            details = f"🎮 {game_name.replace('.exe', '')}"
            state = f"Game HUD • CPU {cpu_pct:.0f}% • GPU {gpu_pct:.0f}%"
        elif timer_text:
            details = f"⏱ {timer_text}"
            state = "Dynamic Island — Borh-edition"
        else:
            details = "Dynamic Island — Borh-edition"
            state = "Дневная тема" if is_day else "Ночная тема"

        changed = (details != self._details) or (state != self._state)
        self._details = details[:128]
        self._state = state[:128]
        if changed and self._connected and (time.monotonic() - self._last_sent_t) >= 3.5:
            self._tick()

    def _emit_status(self, connected: bool, text: str) -> None:
        if connected != self._connected or text != self._status_text:
            self._connected = connected
            self._status_text = text
            self.status_changed.emit(connected, text)

    def _tick(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        threading.Thread(target=self._sync_worker, daemon=True).start()

    def _close_pipe_async(self) -> None:
        threading.Thread(target=self._close_pipe_sync, kwargs={"clear_activity": True}, daemon=True).start()

    def _close_pipe_sync(self, *, clear_activity: bool = False) -> None:
        with self._lock:
            h = self._pipe_handle
            self._pipe_handle = None
            if h is not None and sys.platform == "win32":
                if clear_activity:
                    try:
                        clear_payload = {
                            "cmd": "SET_ACTIVITY",
                            "args": {"pid": os.getpid()},
                            "nonce": str(uuid.uuid4()),
                        }
                        self._write_raw_locked(h, pack_ipc_frame(OP_FRAME, clear_payload))
                        self._write_raw_locked(h, pack_ipc_frame(OP_CLOSE, {}))
                    except Exception:
                        pass
                try:
                    _k32.CloseHandle(h)
                except Exception:
                    pass
        self._emit_status(False, "Отключено" if not self._enabled else "Ожидание подключения")

    def _write_raw_locked(self, handle: int, data: bytes) -> bool:
        written = wt.DWORD(0)
        ok = _k32.WriteFile(handle, data, len(data), ctypes.byref(written), None)
        return bool(ok) and written.value == len(data)

    def _read_nonblocking_locked(self, handle: int, timeout_s: float = 0.6) -> tuple[int, dict] | None:
        deadline = time.monotonic() + timeout_s
        avail = wt.DWORD(0)
        while time.monotonic() < deadline:
            if not _k32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(avail), None):
                return None
            if avail.value >= 8:
                buf = ctypes.create_string_buffer(min(65536, max(4096, avail.value)))
                read_n = wt.DWORD(0)
                if _k32.ReadFile(handle, buf, len(buf), ctypes.byref(read_n), None) and read_n.value >= 8:
                    return unpack_ipc_frame(buf.raw[: read_n.value])
                return None
            time.sleep(0.03)
        return None

    def _sync_worker(self) -> None:
        try:
            running_now = is_discord_running()
            if running_now != self._last_running:
                self._last_running = running_now
                try:
                    self.running_changed.emit(running_now)
                except RuntimeError:
                    pass
            if not self._enabled:
                self._close_pipe_sync(clear_activity=True)
                self._emit_status(False, "Rich Presence выключен в настройках")
                return

            pipe_path = find_discord_ipc_pipe()
            if not pipe_path:
                self._close_pipe_sync(clear_activity=False)
                self._emit_status(False, "Discord Desktop не обнаружен (ipc-pipe закрыт)")
                return

            if not self._client_id:
                self._emit_status(
                    False,
                    "Discord найден — добавьте «Dynamic Island — Borh-edition» в Зарегистрированные игры Discord или укажите Application ID",
                )
                return

            with self._lock:
                if self._pipe_handle is None and sys.platform == "win32":
                    h = _k32.CreateFileW(
                        pipe_path,
                        _GENERIC_READ | _GENERIC_WRITE,
                        0,
                        None,
                        _OPEN_EXISTING,
                        0,
                        None,
                    )
                    if not h or h == _INVALID_HANDLE_VALUE:
                        self._emit_status(False, f"Не удалось открыть {pipe_path}")
                        return
                    hs = pack_ipc_frame(OP_HANDSHAKE, {"v": 1, "client_id": self._client_id})
                    if not self._write_raw_locked(h, hs):
                        _k32.CloseHandle(h)
                        self._emit_status(False, "Ошибка handshake с Discord")
                        return
                    resp = self._read_nonblocking_locked(h, timeout_s=1.2)
                    if resp is None or resp[0] == OP_CLOSE or resp[1].get("evt") == "ERROR":
                        err_msg = ""
                        if resp and isinstance(resp[1], dict):
                            err_msg = str(resp[1].get("data", {}).get("message") or resp[1].get("message") or "")
                        _k32.CloseHandle(h)
                        self._emit_status(False, f"Discord отклонил Application ID ({err_msg or 'Invalid Client ID'})")
                        return
                    self._pipe_handle = h
                    self._last_sent_sig = ""

                sig = f"{self._details}|{self._state}"
                if sig != self._last_sent_sig and self._pipe_handle is not None:
                    activity: dict = {
                        "details": self._details if len(self._details) >= 2 else "Dynamic Island",
                        "state": self._state if len(self._state) >= 2 else "Borh-edition",
                        "timestamps": {"start": self._start_ts},
                        "assets": {
                            "large_image": "island_logo",
                            "large_text": "Dynamic Island — Borh-edition",
                        },
                    }
                    frame = pack_ipc_frame(
                        OP_FRAME,
                        {
                            "cmd": "SET_ACTIVITY",
                            "args": {"pid": os.getpid(), "activity": activity},
                            "nonce": str(uuid.uuid4()),
                        },
                    )
                    if not self._write_raw_locked(self._pipe_handle, frame):
                        try:
                            _k32.CloseHandle(self._pipe_handle)
                        except Exception:
                            pass
                        self._pipe_handle = None
                        self._emit_status(False, "Потеряна связь с Discord IPC, переподключение...")
                        return
                    self._read_nonblocking_locked(self._pipe_handle, timeout_s=0.25)
                    self._last_sent_sig = sig
                    self._last_sent_t = time.monotonic()

            self._emit_status(True, f"Подключено к Discord Rich Presence ({self._details})")
        except Exception as exc:
            self._emit_status(False, f"Discord IPC ошибка: {exc}")
        finally:
            self._syncing = False

    def is_discord_running(self) -> bool:
        """Return True if a local Discord IPC pipe or process is present without blocking the UI thread."""
        if find_discord_ipc_pipe() is not None:
            self._last_running = True
            return True
        if self._last_running is not None:
            return bool(self._last_running)
        running = is_discord_running()
        self._last_running = running
        return running

    def _apply_audio_mute_bg(self, *, mic_muted: bool | None = None, discord_deafened: bool | None = None) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        dev_in = "default"
        if self._config_mgr is not None:
            try:
                dev_in = self._config_mgr.config.input_device_id or "default"
            except Exception:
                dev_in = "default"

        def _work() -> None:
            try:
                if mic_muted is not None:
                    bridge.set_mute(bool(mic_muted), "capture", dev_in)
            except Exception:
                pass
            try:
                if discord_deafened is not None:
                    for hint in ("discord", "discordptb", "discordcanary", "vesktop"):
                        if bridge.set_app_mute(hint, bool(discord_deafened), "default"):
                            break
            except Exception:
                pass

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(0.03)

    def _uia_click_discord_actions(self, actions: list[str]) -> None:
        if sys.platform != "win32" or not actions:
            return
        patterns = {
            "mute_on": r"(?i)^(Mute|((Откл\.?|Выкл\.?|Отключить|Выключить|Заглушить)\s+микрофон))$",
            "mute_off": r"(?i)^(Unmute|((Вкл\.?|Включить|Разглушить)\s+микрофон))$",
            "deaf_on": r"(?i)^(Deafen|((Откл\.?|Выкл\.?|Отключить|Выключить|Заглушить)\s+звук))$",
            "deaf_off": r"(?i)^(Undeafen|((Вкл\.?|Включить|Разглушить)\s+звук))$",
        }
        regexes = [patterns[a] for a in actions if a in patterns]
        if not regexes:
            return

        def _work() -> None:
            try:
                import subprocess

                regex_ps = ", ".join(f"'{rx}'" for rx in regexes)
                ps = (
                    "$ErrorActionPreference = 'SilentlyContinue'\n"
                    "Add-Type -AssemblyName UIAutomationClient\n"
                    "Add-Type -AssemblyName UIAutomationTypes\n"
                    "$procs = Get-Process -Name 'Discord','DiscordPTB','DiscordCanary','Vesktop' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }\n"
                    f"$rxList = @({regex_ps})\n"
                    "foreach ($pr in $procs) {\n"
                    "    $el = [System.Windows.Automation.AutomationElement]::FromHandle($pr.MainWindowHandle)\n"
                    "    if ($el) {\n"
                    "        $cond = New-Object System.Windows.Automation.PropertyCondition(\n"
                    "            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,\n"
                    "            [System.Windows.Automation.ControlType]::Button\n"
                    "        )\n"
                    "        foreach ($rx in $rxList) {\n"
                    "            $btns = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)\n"
                    "            foreach ($b in $btns) {\n"
                    "                $nm = ([string]$b.Current.Name).Trim()\n"
                    "                if ($nm -match $rx) {\n"
                    "                    $pat = $null\n"
                    "                    if ($b.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) {\n"
                    "                        $pat.Invoke()\n"
                    "                        Start-Sleep -Milliseconds 70\n"
                    "                        break\n"
                    "                    }\n"
                    "                }\n"
                    "            }\n"
                    "        }\n"
                    "    }\n"
                    "}\n"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                    capture_output=True,
                    timeout=3.5,
                    creationflags=0x08000000,
                )
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def set_voice_muted(self, muted: bool) -> bool:
        """Toggle Discord mic mute; unmuting while deafened automatically un-deafens sound too."""
        want_mute = bool(muted)
        was_deafened = self._deafened
        self._muted = want_mute
        if not want_mute:
            self._deafened = False
        ok = self._send_voice_ipc(
            "SET_VOICE_SETTINGS",
            {"mute": self._muted, "deaf": self._deafened},
        )
        self._apply_audio_mute_bg(
            mic_muted=self._muted,
            discord_deafened=(False if (was_deafened and not self._deafened) else None),
        )
        if not ok:
            acts = ["mute_on"] if want_mute else (["deaf_off", "mute_off"] if was_deafened else ["mute_off"])
            self._uia_click_discord_actions(acts)
        self.voice_state_changed.emit(self._in_voice, self._muted, self._deafened)
        return ok or is_discord_running()

    def set_voice_deafened(self, deafened: bool) -> bool:
        """Toggle Discord deafen; deafening disables both sound and mic, un-deafening restores both."""
        want_deaf = bool(deafened)
        self._deafened = want_deaf
        self._muted = want_deaf
        ok = self._send_voice_ipc(
            "SET_VOICE_SETTINGS",
            {"deaf": self._deafened, "mute": self._muted},
        )
        self._apply_audio_mute_bg(mic_muted=self._muted, discord_deafened=self._deafened)
        if not ok:
            acts = ["deaf_on"] if want_deaf else ["deaf_off", "mute_off"]
            self._uia_click_discord_actions(acts)
        self.voice_state_changed.emit(self._in_voice, self._muted, self._deafened)
        return ok or is_discord_running()

    def toggle_mute(self) -> bool:
        """Flip current Discord mic mute state and return new muted value."""
        self.set_voice_muted(not self._muted)
        return self._muted

    def toggle_deafen(self) -> bool:
        """Flip current Discord deafen state and return new deafened value."""
        self.set_voice_deafened(not self._deafened)
        return self._deafened

    def leave_voice_channel(self) -> bool:
        """Leave the active Discord voice channel via IPC pipe + UIA button fallback."""
        ok = self._send_voice_ipc("SELECT_VOICE_CHANNEL", {"channel_id": None})
        self._in_voice = False
        self._muted = False
        self._deafened = False
        self._apply_audio_mute_bg(mic_muted=False, discord_deafened=False)
        self.voice_state_changed.emit(self._in_voice, self._muted, self._deafened)
        if sys.platform == "win32":
            def _uia_leave() -> None:
                try:
                    import subprocess
                    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$procs = Get-Process -Name 'Discord','DiscordPTB','DiscordCanary' -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
foreach ($pr in $procs) {
    $el = [System.Windows.Automation.AutomationElement]::FromHandle($pr.MainWindowHandle)
    if ($el) {
        $cond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        )
        $btns = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        foreach ($b in $btns) {
            $nm = [string]$b.Current.Name
            if ($nm -match 'Disconnect|Отключиться|Leave Voice|Отключить голос') {
                $pat = $null
                if ($b.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) {
                    $pat.Invoke()
                    break
                }
            }
        }
    }
}
"""
                    subprocess.run(
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                        capture_output=True,
                        timeout=3.5,
                        creationflags=0x08000000,
                    )
                except Exception:
                    pass

            threading.Thread(target=_uia_leave, daemon=True).start()
        return ok

    def _send_voice_ipc(self, cmd: str, args: dict) -> bool:
        with self._lock:
            h = self._pipe_handle
            if h is None:
                return False
            frame = pack_ipc_frame(
                OP_FRAME,
                {"cmd": cmd, "args": args, "nonce": str(uuid.uuid4())},
            )
            ok = self._write_raw_locked(h, frame)
            if ok:
                self._read_nonblocking_locked(h, timeout_s=0.15)
            return ok
