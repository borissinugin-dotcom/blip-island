"""Auto-updater service for Blip (checks GitHub Releases or local manifest, preserves PRO license)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from borh_island.core.licensing import APP_PRODUCT_NAME, APP_VERSION

DEFAULT_UPDATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_PRODUCT_NAME / "updates"
GITHUB_REPO = "borissinugin-dotcom/blip-island"


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


def check_github_updates(repo: str = GITHUB_REPO, timeout: float = 8.0) -> UpdateInfo:
    """Query GitHub Releases API for the latest published release and .exe asset."""
    try:
        import httpx

        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"BlipIsland/{APP_VERSION}"}
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 404:
                return UpdateInfo(False, APP_VERSION, APP_VERSION, error="Релизы на GitHub пока не найдены")
            resp.raise_for_status()
            data = resp.json()

        latest = str(data.get("tag_name") or APP_VERSION).strip().lstrip("vV")
        notes = str(data.get("body") or "").strip()
        exe_url = ""
        for asset in data.get("assets") or []:
            name = str(asset.get("name") or "").lower()
            if name.endswith(".exe"):
                exe_url = str(asset.get("browser_download_url") or "").strip()
                break
        zip_url = str(data.get("zipball_url") or "").strip()
        dl_url = exe_url if getattr(sys, "frozen", False) else (zip_url or exe_url)
        if not dl_url:
            dl_url = str(data.get("html_url") or "").strip()
        avail = bool(dl_url) and _parse_ver(latest) > _parse_ver(APP_VERSION)
        return UpdateInfo(avail, APP_VERSION, latest, dl_url, "", notes)
    except Exception as exc:
        return UpdateInfo(False, APP_VERSION, APP_VERSION, error=str(exc))


def check_for_updates(
    update_dir: Path | str = DEFAULT_UPDATE_DIR,
    timeout: float = 6.0,
    *,
    allow_network: bool = False,
) -> UpdateInfo:
    """Read local manifest JSON if present; optionally fall back to GitHub Releases."""
    try:
        base_dir = Path(update_dir)
        manifest_path = base_dir if base_dir.suffix.lower() == ".json" else (base_dir / "manifest.json")
        if manifest_path.exists():
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
        if not allow_network:
            return UpdateInfo(False, APP_VERSION, APP_VERSION, error=str(exc))
    if allow_network:
        return check_github_updates(timeout=timeout)
    return UpdateInfo(False, APP_VERSION, APP_VERSION, error="")


def download_and_stage_update(info: UpdateInfo, project_dir: Path | None = None) -> tuple[bool, str]:
    """Download/copy update (.exe from GitHub or local .zip), stage, and spawn self-replacing updater."""
    if not info.available or not info.download_url:
        return False, "Обновление не требуется"

    # Remote GitHub update flow (.exe for frozen builds, zipball for Python source)
    if info.download_url.startswith(("http://", "https://")):
        try:
            import httpx

            stage_root = Path(tempfile.mkdtemp(prefix="blip_update_"))
            is_exe = info.download_url.lower().endswith(".exe")
            if is_exe:
                new_exe = stage_root / "BlipIsland_new.exe"
                with httpx.Client(timeout=180.0, follow_redirects=True) as client:
                    with client.stream("GET", info.download_url) as resp:
                        resp.raise_for_status()
                        with new_exe.open("wb") as fh:
                            for chunk in resp.iter_bytes(chunk_size=131072):
                                if chunk:
                                    fh.write(chunk)
                if not getattr(sys, "frozen", False):
                    return True, f"Новый .exe скачан в {new_exe}"
                pid = os.getpid()
                cur_exe = Path(sys.executable).resolve()
                bat_path = stage_root / "apply_exe_update.bat"
                bat_content = (
                    "@echo off\r\n"
                    "timeout /t 2 /nobreak >nul\r\n"
                    f":waitloop\r\ntasklist /FI \"PID eq {pid}\" 2>nul | find \"{pid}\" >nul && (timeout /t 1 /nobreak >nul & goto waitloop)\r\n"
                    f"move /Y \"{new_exe}\" \"{cur_exe}\" >nul\r\n"
                    f"start \"\" \"{cur_exe}\" --replace\r\n"
                )
                bat_path.write_text(bat_content, encoding="utf-8")
                subprocess.Popen(
                    ["cmd.exe", "/c", str(bat_path)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )
                return True, f"Обновление до v{info.latest_version} загружено. Перезапуск..."

            # Source zipball update
            zip_path = stage_root / "source.zip"
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                with client.stream("GET", info.download_url, headers={"User-Agent": f"BlipIsland/{APP_VERSION}"}) as resp:
                    resp.raise_for_status()
                    with zip_path.open("wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            if chunk:
                                fh.write(chunk)
            extract_dir = stage_root / "src"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
            src_root = subdirs[0] if len(subdirs) == 1 else extract_dir
            target_dir = project_dir or Path(__file__).resolve().parent.parent.parent
            pid = os.getpid()
            bat_path = stage_root / "apply_src_update.bat"
            launch_exe = sys.executable
            main_py = target_dir / "main.py"
            bat_content = (
                "@echo off\r\n"
                "timeout /t 2 /nobreak >nul\r\n"
                f":waitloop\r\ntasklist /FI \"PID eq {pid}\" 2>nul | find \"{pid}\" >nul && (timeout /t 1 /nobreak >nul & goto waitloop)\r\n"
                f"xcopy /E /Y /I \"{src_root}\\*\" \"{target_dir}\\\" >nul\r\n"
                f"start \"\" \"{launch_exe}\" \"{main_py}\" --replace\r\n"
            )
            bat_path.write_text(bat_content, encoding="utf-8")
            subprocess.Popen(
                ["cmd.exe", "/c", str(bat_path)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            return True, f"Исходники обновлены до v{info.latest_version}. Перезапуск..."
        except Exception as exc:
            return False, f"Ошибка скачивания: {exc}"

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
