"""Screen recording orchestrator for H.264 MP4 (Media Foundation) and animated GIF."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageGrab

from .native_bridge import NativeBridge


class ScreenRecorder:
    """Manages active screen recording sessions in MP4 (H.264) or GIF format."""

    def __init__(self, bridge: NativeBridge, output_dir: Path) -> None:
        self.bridge = bridge
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._recording = False
        self._mode = "mp4"
        self._out_path: Path | None = None
        self._started_at = 0.0
        self._gif_thread: threading.Thread | None = None
        self._gif_stop = threading.Event()
        self._on_finished: Callable[[Path], None] | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_seconds(self) -> float:
        if not self._recording:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def start(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        fmt: str = "mp4",
        fps: int = 30,
        output_dir: Path | None = None,
        on_finished: Callable[[Path], None] | None = None,
        audio_mode: str = "system",
        target_hwnd: int = 0,
    ) -> Path:
        """Start recording screen region (x, y, w, h) or specific window HWND into output_dir."""
        if self._recording:
            raise RuntimeError("Recording already in progress")
        target_dir = Path(output_dir) if output_dir else self.output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "gif" if fmt.lower() == "gif" else "mp4"
        out_path = target_dir / f"recording_{stamp}.{ext}"

        self._mode = ext
        self._out_path = out_path
        self._started_at = time.monotonic()
        self._on_finished = on_finished

        if ext == "mp4":
            self.bridge.start_mp4_recording(
                x, y, w, h, fps, out_path, audio_mode=audio_mode, target_hwnd=int(target_hwnd or 0)
            )
            self._recording = True
        else:
            self._gif_stop.clear()
            self._recording = True
            self._gif_thread = threading.Thread(
                target=self._record_gif_loop,
                args=(x, y, w, h, max(5, min(20, fps)), out_path),
                daemon=True,
            )
            self._gif_thread.start()
        return out_path

    def _record_gif_loop(self, x: int, y: int, w: int, h: int, fps: int, out_path: Path) -> None:
        frames: list[Image.Image] = []
        interval = 1.0 / fps
        bbox = (x, y, x + w, y + h)
        max_side = 1280
        try:
            while not self._gif_stop.is_set() and len(frames) < fps * 180:
                t0 = time.monotonic()
                snap = ImageGrab.grab(bbox=bbox, all_screens=True)
                if snap.width > max_side or snap.height > max_side:
                    snap.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                frames.append(snap.convert("P", palette=Image.Palette.ADAPTIVE, colors=192))
                dt = time.monotonic() - t0
                if dt < interval:
                    self._gif_stop.wait(interval - dt)
            if frames:
                dur_ms = int(round(1000 / fps))
                frames[0].save(
                    out_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=dur_ms,
                    loop=0,
                    optimize=True,
                )
        finally:
            self._recording = False
            if self._on_finished and out_path.exists():
                self._on_finished(out_path)

    def stop(self) -> Path | None:
        """Stop current recording and return saved file path if created."""
        if not self._recording:
            return None
        path = self._out_path
        if self._mode == "mp4":
            ok = self.bridge.stop_mp4_recording()
            self._recording = False
            if ok and path and path.exists():
                if self._on_finished:
                    self._on_finished(path)
                return path
            return path if (path and path.exists()) else None
        else:
            self._gif_stop.set()
            if self._gif_thread:
                self._gif_thread.join(timeout=8.0)
                self._gif_thread = None
            self._recording = False
            return path if (path and path.exists()) else None
