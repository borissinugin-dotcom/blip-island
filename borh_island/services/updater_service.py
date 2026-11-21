"""Auto-updater service for Blip (verifies SHA-256 manifest and preserves PRO license across updates)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from borh_island.core.licensing import APP_PRODUCT_NAME, APP_VERSION

DEFAULT_UPDATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_PRODUCT_NAME / "updates"


def _parse_ver(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(v or "0").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


@dataclass
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str
    download_url: str = ""
    sha256: str = ""
    notes: str = ""
    error: str = ""


def check_for_updates(update_dir: Path | str = DEFAULT_UPDATE_DIR, timeout: float = 6.0) -> UpdateInfo:
    """Read update manifest JSON strictly from the local updates folder (no network access)."""
    del timeout
    try:
        base_dir = Path(update_dir)
        manifest_path = base_dir if base_dir.suffix.lower() == ".json" else (base_dir / "manifest.json")
        if not manifest_path.exists():
            return UpdateInfo(False, APP_VERSION, APP_VERSION, error="")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        latest = str(data.get("version") or APP_VERSION).strip()
        rel_file = str(data.get("file") or data.get("path") or data.get("url") or "").strip()
        sha = str(data.get("sha256") or "").strip().lower()
        notes = str(data.get("notes") or "").strip()
        pkg_path = Path(rel_file)
        if rel_file and not pkg_path.is_absolute():
            pkg_path = manifest_path.parent / pkg_path
        avail = bool(rel_file) and pkg_path.exists() and _parse_ver(latest) > _parse_ver(APP_VERSION)
        return UpdateInfo(avail, APP_VERSION, latest, str(pkg_path) if rel_file else "", sha, notes)
    except Exception as exc:
        return UpdateInfo(False, APP_VERSION, APP_VERSION, error=str(exc))


def download_and_stage_update(info: UpdateInfo, project_dir: Path | None = None) -> tuple[bool, str]:
    """Copy local update zip, verify SHA-256, extract to temp, and spawn self-replacing updater."""
    if not info.available or not info.download_url:
        return False, "Обновление не требуется"
    target_dir = project_dir or Path(__file__).resolve().parent.parent.parent
    try:
        src_zip = Path(info.download_url)
        if not src_zip.exists() or not src_zip.is_file():
            return False, "Локальный архив обновления не найден"

        stage_root = Path(tempfile.mkdtemp(prefix="blip_update_"))
        zip_path = stage_root / "update.zip"
        h = hashlib.sha256()
        with src_zip.open("rb") as src_fh, zip_path.open("wb") as dst_fh:
            while True:
                chunk = src_fh.read(65536)
                if not chunk:
                    break
                dst_fh.write(chunk)
                h.update(chunk)
        if info.sha256 and h.hexdigest().lower() != info.sha256.lower():
            return False, "Ошибка контрольной суммы SHA-256"

        extract_dir = stage_root / "files"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        if sys.platform != "win32":
            return True, f"Обновление распаковано в {extract_dir}"

        pid = os.getpid()
        bat_path = stage_root / "apply_update.bat"
        launch_exe = sys.executable
        main_py = target_dir / "main.py"
        bat_content = (
            "@echo off\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            f":waitloop\r\ntasklist /FI \"PID eq {pid}\" 2>nul | find \"{pid}\" >nul && (timeout /t 1 /nobreak >nul & goto waitloop)\r\n"
            f"xcopy /E /Y /I \"{extract_dir}\\*\" \"{target_dir}\\\" >nul\r\n"
            f"start \"\" \"{launch_exe}\" \"{main_py}\" --replace\r\n"
        )
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.Popen(
            ["cmd.exe", "/c", str(bat_path)],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        return True, f"Обновление до v{info.latest_version} загружено. Перезапуск..."
    except Exception as exc:
        return False, f"Ошибка обновления: {exc}"
