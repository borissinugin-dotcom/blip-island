"""Live hot-reload watcher allowing in-place UI/service module reloads and safe seamless restarts."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Iterable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# Modules safe to reload dynamically in-place while keeping SQLite, WinRT, and HTTP sockets alive
UI_RELOAD_ORDER: tuple[str, ...] = (
    "borh_island.core.i18n",
    "borh_island.services.text_tools",
    "borh_island.services.reminder_parser",
    "borh_island.services.lyrics_service",
    "borh_island.services.weather_service",
    "borh_island.services.command_service",
    "borh_island.ui.atmosphere",
    "borh_island.ui.theme",
    "borh_island.ui.color_wheel_dialog",
    "borh_island.ui.characters",
    "borh_island.ui.live_cards",
    "borh_island.ui.tabs.home_tab",
    "borh_island.ui.tabs.character_tab",
    "borh_island.ui.tabs.text_tab",
    "borh_island.ui.tabs.notes_tab",
    "borh_island.ui.tabs.weather_tab",
    "borh_island.ui.tabs.controls_tab",
    "borh_island.ui.expanded_panel",
    "borh_island.ui.command_bar",
    "borh_island.ui.screen_tools",
    "borh_island.ui.settings_window",
)

CORE_RESTART_FILES = {
    "main.py",
    "config.py",
    "security.py",
    "storage.py",
    "win32_api.py",
    "native_bridge.py",
    "winrt_worker.py",
    "media_service.py",
    "clipboard_service.py",
    "system_service.py",
    "hot_reload.py",
    "island_window.py",
}


class HotReloadManager(QObject):
    """Watches package source files and triggers hot UI reload or seamless app relaunch."""

    ui_reload_requested = pyqtSignal(list)  # list[str] changed relative paths
    full_restart_requested = pyqtSignal(list)  # list[str] changed relative paths
    native_recompile_requested = pyqtSignal()

    def __init__(self, package_root: Path, enabled: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.package_root = Path(package_root)
        self.enabled = enabled
        self._mtimes: dict[Path, float] = {}
        self._pending_paths: set[Path] = set()

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(15_000)
        self._scan_timer.timeout.connect(self._check_files)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self._dispatch_changes)

        self._snapshot_mtimes()
        if self.enabled:
            self._scan_timer.start()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self._snapshot_mtimes()
            self._scan_timer.start()
        else:
            self._scan_timer.stop()
            self._debounce.stop()
            self._pending_paths.clear()

    def _iter_watched_files(self) -> Iterable[Path]:
        if not self.package_root.exists():
            return ()
        files: list[Path] = []
        skip_dirs = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
        stack = [self.package_root]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        name = entry.name
                        if entry.is_dir(follow_symlinks=False):
                            if name not in skip_dirs:
                                stack.append(Path(entry.path))
                        elif name.endswith((".py", ".cs")):
                            files.append(Path(entry.path))
            except OSError:
                continue
        return files

    def _snapshot_mtimes(self) -> None:
        self._mtimes = {}
        for p in self._iter_watched_files():
            try:
                self._mtimes[p] = p.stat().st_mtime
            except OSError:
                pass

    def _check_files(self) -> None:
        if not self.enabled:
            return
        changed: list[Path] = []
        current_files = list(self._iter_watched_files())
        for p in current_files:
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            prev = self._mtimes.get(p)
            if prev is None or mt > prev + 1e-6:
                self._mtimes[p] = mt
                changed.append(p)
        if changed:
            self._pending_paths.update(changed)
            self._debounce.start()

    def _dispatch_changes(self) -> None:
        if not self._pending_paths:
            return
        paths = sorted(self._pending_paths)
        self._pending_paths.clear()
        rel_names = [str(p.relative_to(self.package_root)) for p in paths]

        if any(p.suffix.lower() == ".cs" for p in paths):
            self.native_recompile_requested.emit()
            if all(p.suffix.lower() == ".cs" for p in paths):
                return

        needs_restart = any(p.name in CORE_RESTART_FILES for p in paths if p.suffix.lower() == ".py")
        if needs_restart:
            self.full_restart_requested.emit(rel_names)
        else:
            self.ui_reload_requested.emit(rel_names)

    @staticmethod
    def reload_ui_modules() -> list[str]:
        """Reload loaded UI & stateless service modules in topological order."""
        reloaded: list[str] = []
        for mod_name in UI_RELOAD_ORDER:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                importlib.reload(mod)
                reloaded.append(mod_name)
        return reloaded
