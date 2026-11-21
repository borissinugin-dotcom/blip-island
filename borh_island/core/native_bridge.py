"""Python wrapper that compiles and calls BorhWinNative.cs via Windows csc.exe."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

CSC_PATH = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
CS_SOURCE = Path(__file__).resolve().parent.parent / "native" / "BorhWinNative.cs"
CREATE_NO_WINDOW = 0x08000000


def _ascii_staging_dir() -> Path:
    """Return a writable ASCII directory so csc.exe resource generation works under non-ASCII user profiles."""
    for base in (
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "BorhIsland_build",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp" / "BorhIsland_build",
    ):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write_probe"
            probe.write_text("ok", encoding="ascii")
            probe.unlink()
            return base
        except OSError:
            continue
    return Path(tempfile.gettempdir())


class NativeBridge:
    """Manages compilation and invocations of BorhWinNative.exe."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.exe_path = self.data_dir / "BorhWinNative.exe"
        self.hash_path = self.data_dir / "BorhWinNative.sha256"
        self._compile_lock = threading.Lock()
        self._loopback_proc: subprocess.Popen[str] | None = None
        self._rec_proc: subprocess.Popen[str] | None = None

    def ensure_compiled(self) -> Path:
        with self._compile_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            src_hash = hashlib.sha256(CS_SOURCE.read_bytes()).hexdigest()
            if (
                self.exe_path.exists()
                and self.hash_path.exists()
                and self.hash_path.read_text(encoding="utf-8").strip() == src_hash
            ):
                return self.exe_path
            if not CSC_PATH.exists():
                raise FileNotFoundError(f"csc.exe not found at {CSC_PATH}")

            stage = _ascii_staging_dir()
            stage_cs = stage / "BorhWinNative.cs"
            stage_exe = stage / "BorhWinNative.exe"
            shutil.copy2(CS_SOURCE, stage_cs)

            env = os.environ.copy()
            env["TMP"] = str(stage)
            env["TEMP"] = str(stage)
            cmd = [
                str(CSC_PATH),
                "/nologo",
                "/nowin32manifest",
                "/optimize+",
                "/platform:x64",
                "/target:exe",
                f"/out:{stage_exe}",
                "/reference:System.dll",
                "/reference:System.Core.dll",
                "/reference:System.Drawing.dll",
                "/reference:System.Management.dll",
                str(stage_cs),
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="oem",
                errors="replace",
                env=env,
                creationflags=CREATE_NO_WINDOW,
                timeout=30,
            )
            if res.returncode != 0:
                raise RuntimeError(f"Native helper compile error: {res.stdout}\n{res.stderr}")
            shutil.copy2(stage_exe, self.exe_path)
            self.hash_path.write_text(src_hash, encoding="utf-8")
            return self.exe_path

    def _run(self, *args: str, timeout: float = 5.0) -> str:
        exe = self.ensure_compiled()
        res = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or f"Command {args[0]} failed ({res.returncode})")
        return res.stdout.strip()

    def get_audio_devices(self) -> dict[str, list[dict]]:
        raw = self._run("audio-devices")
        return json.loads(raw)

    def get_volume(self, flow: str = "render", device_id: str = "default") -> dict:
        raw = self._run("volume-get", flow, device_id or "default")
        return json.loads(raw)

    def set_volume(self, level: int, flow: str = "render", device_id: str = "default") -> None:
        self._run("volume-set", flow, device_id or "default", str(max(0, min(100, int(level)))))

    def set_mute(self, muted: bool, flow: str = "render", device_id: str = "default") -> None:
        self._run("mute-set", flow, device_id or "default", "1" if muted else "0")

    def get_app_volume(self, proc_hint: str = "spotify", device_id: str = "default") -> dict:
        raw = self._run("app-volume-get", proc_hint or "spotify", device_id or "default")
        return json.loads(raw)

    def set_app_volume(self, proc_hint: str = "spotify", level: int = 100, device_id: str = "default") -> bool:
        try:
            raw = self._run(
                "app-volume-set",
                proc_hint or "spotify",
                device_id or "default",
                str(max(0, min(100, int(level)))),
            )
            return bool(json.loads(raw).get("ok"))
        except Exception:
            return False

    def set_app_mute(self, proc_hint: str, muted: bool, device_id: str = "default") -> bool:
        try:
            raw = self._run(
                "app-mute-set",
                proc_hint,
                device_id or "default",
                "1" if muted else "0",
            )
            return bool(json.loads(raw).get("ok"))
        except Exception:
            return False

    def list_app_sessions(self, device_id: str = "default") -> list[dict]:
        """Return deduplicated per-app render audio sessions on the specified output device."""
        try:
            raw = self._run("app-sessions", device_id or "default")
            items = json.loads(raw)
        except Exception:
            return []
        if not isinstance(items, list):
            return []

        merged: dict[str, dict] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            proc = str(item.get("process", "")).strip()
            if not proc:
                continue
            key = "__system__" if item.get("system") or proc == "__system__" else proc.removesuffix(".exe").lower()
            if key == "borhwinnative":
                continue
            vol = max(0, min(100, int(item.get("volume", 100))))
            muted = bool(item.get("muted", False))
            active = bool(item.get("active", False))
            title = str(item.get("title", "")).strip() or proc
            exe = str(item.get("exe", "")).strip()
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "key": key,
                    "process": proc,
                    "title": "Системные звуки" if key == "__system__" else title,
                    "exe": exe,
                    "volume": vol,
                    "muted": muted,
                    "active": active,
                    "system": key == "__system__",
                }
            else:
                # Prefer active session properties and populate missing exe/title metadata
                if active and not existing["active"]:
                    existing["volume"] = vol
                    existing["muted"] = muted
                    existing["active"] = True
                elif existing["active"] == active:
                    existing["volume"] = max(existing["volume"], vol)
                    existing["muted"] = existing["muted"] and muted
                if not existing["exe"] and exe:
                    existing["exe"] = exe
        return sorted(
            merged.values(),
            key=lambda s: (0 if s["active"] else 1, 1 if s["system"] else 0, s["title"].lower()),
        )

    def apply_audio_profile(self, rules: dict[str, dict], device_id: str = "default") -> int:
        """Apply a mapping of `{app_key: {'volume': int, 'muted': bool}}` across open audio sessions."""
        if not isinstance(rules, dict) or not rules:
            return 0
        parts: list[str] = []
        for app_key, entry in rules.items():
            if not isinstance(entry, dict):
                continue
            clean_key = str(app_key).replace(";", "").replace("=", "").strip()
            if not clean_key:
                continue
            vol = max(0, min(100, int(entry.get("volume", 100))))
            mut = 1 if entry.get("muted", False) else 0
            parts.append(f"{clean_key}={vol}:{mut}")
        if not parts:
            return 0
        try:
            raw = self._run("app-profile-apply", device_id or "default", ";".join(parts))
            return int(json.loads(raw).get("applied", 0))
        except Exception:
            return 0

    def get_brightness(self) -> int:
        try:
            return int(self._run("brightness-get"))
        except Exception:
            return -1

    def set_brightness(self, level: int) -> None:
        self._run("brightness-set", str(max(0, min(100, int(level)))))

    # ------------------------------------------------------------------ WASAPI Loopback Spectrum
    def start_loopback_stream(
        self,
        callback: Callable[[list[float]], None],
        device_id: str = "default",
    ) -> None:
        self.stop_loopback_stream()
        exe = self.ensure_compiled()
        proc = subprocess.Popen(
            [str(exe), "loopback-stream", device_id or "default"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self._loopback_proc = proc

        def _reader() -> None:
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        bands = [float(x) for x in line.split(",")]
                        callback(bands)
                    except ValueError:
                        continue
            except Exception:
                pass

        threading.Thread(target=_reader, daemon=True).start()

    def update_loopback_filter(self, source_hint: str, playing: bool) -> None:
        """Restrict loopback visualization to only the active music process (excluding Discord/calls)."""
        proc = self._loopback_proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return
        try:
            if not playing:
                msg = "PAUSE\n"
            else:
                hint = (source_hint or "any").strip().lower()
                for prefix, tok in (
                    ("spotify", "spotify"),
                    ("yandex", "yandexmusic"),
                    ("aimp", "aimp"),
                    ("winamp", "winamp"),
                    ("foobar", "foobar2000"),
                    ("apple", "applemusic"),
                    ("itunes", "itunes"),
                    ("vlc", "vlc"),
                    ("chrome", "chrome"),
                    ("msedge", "msedge"),
                    ("edge", "msedge"),
                    ("firefox", "firefox"),
                    ("opera", "opera"),
                    ("brave", "brave"),
                ):
                    if prefix in hint:
                        hint = tok
                        break
                msg = f"ALLOW:{hint or 'any'}\n"
            proc.stdin.write(msg)
            proc.stdin.flush()
        except Exception:
            pass

    def stop_loopback_stream(self) -> None:
        proc = self._loopback_proc
        self._loopback_proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write("STOP\n")
                proc.stdin.flush()
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ MP4 H.264 Recording
    def start_mp4_recording(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        fps: int,
        out_path: Path,
        audio_mode: str = "none",
        target_hwnd: int = 0,
    ) -> None:
        self.stop_mp4_recording()
        exe = self.ensure_compiled()
        amode = audio_mode if audio_mode in ("none", "system", "both") else "none"
        proc = subprocess.Popen(
            [str(exe), "mp4-record", str(x), str(y), str(w), str(h), str(fps), str(out_path), amode, str(int(target_hwnd or 0))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        self._rec_proc = proc
        if proc.stdout:
            ready = proc.stdout.readline().strip()
            if ready != "READY":
                err = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(f"MP4 recorder failed to start: {ready} {err}")

    def stop_mp4_recording(self) -> bool:
        proc = self._rec_proc
        self._rec_proc = None
        if proc is None:
            return False
        try:
            if proc.stdin:
                proc.stdin.write("STOP\n")
                proc.stdin.flush()
            out, _ = proc.communicate(timeout=8.0)
            return proc.returncode == 0 and "SAVED:" in out
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return False
