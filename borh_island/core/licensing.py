"""Blip licensing, 3-day trial protection, Ed25519 verification, DPAPI persistence, and developer codes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from borh_island.core.security import decrypt_secret, encrypt_secret

APP_PRODUCT_NAME = "Blip"
APP_VERSION = "1.0.11"
WORKER_BASE_URL = "https://blip-license-worker.blip-borh.workers.dev"
ED25519_PUBLIC_KEY_B64U = "lue3Ld1hP5dYrUlXjrDRxmv6UWKl_vKAOjzmHKWJH8w"

TRIAL_DURATION_SECONDS = 3 * 3600  # 3 hours
FREE_CHARACTER_IDS: tuple[str, ...] = ("liquid", "orb", "bot")
DEV_EXCLUSIVE_CHARACTER_IDS: tuple[str, ...] = ("borscht", "purple_star", "kamaz", "meimei")

_DEV_SALT = "BlipDevSalt::2026::Ed25519"
_MASTER_BORH_HASH = "b831ffb499b2a3a9a6c56da7001d1a8114552f9386a487fb1c3011791e6701a0"
CODE_POLICIES_URL = "https://raw.githubusercontent.com/borissinugin-dotcom/blip-island/main/code_policies.json"
# Salted SHA-256 digests of developer codes (never stored in plaintext)
_DEV_CODE_HASHES: dict[str, dict[str, object]] = {
    "ce872be183e8674204aad6395a4e4f60845a6510081a973214f2022889ab2595": {
        "chars": ("purple_star",),
        "all_unlocked": False,
        "label": "Purple Star + PRO",
    },
    "b831ffb499b2a3a9a6c56da7001d1a8114552f9386a487fb1c3011791e6701a0": {
        "chars": ("borscht", "purple_star", "kamaz", "meimei"),
        "all_unlocked": True,
        "label": "Full Developer Edition",
    },
    "9177a58a01c0c65dc1bb98c10c418ce83ec30d69d3882e5ce5f9a8232f0518c2": {
        "chars": ("kamaz",),
        "all_unlocked": False,
        "label": "KAMAZ + PRO",
    },
    "50243267e7da36155d2e8dec6565132c4eab2c6bfbe37ef55b52d32dc92b32b3": {
        "chars": ("meimei",),
        "all_unlocked": False,
        "label": "Mei Mei + PRO",
    },
    "ff0f3d372da7c50d331548f1ce05c74f743a0e780e4bd0f0a0e2b6efe75e0bab": {
        "chars": ("borscht", "purple_star", "kamaz", "meimei"),
        "all_unlocked": True,
        "label": "Full Developer Edition",
    },
    "148f0bed4a8bfd28eb96f2b2162a3356fa938cc2e664a7d4e5dc116280c29f20": {
        "chars": ("borscht", "purple_star", "kamaz", "meimei"),
        "all_unlocked": True,
        "label": "Full Developer Edition",
    },
    "7c7f15fbd83b699d3e936fecd53d814f45878650fdff5ce1dd457d4470dcf3d5": {
        "chars": ("borscht", "purple_star", "kamaz", "meimei"),
        "all_unlocked": True,
        "label": "Full Developer Edition",
    },
    "ccebcd27c45983769cc7fdf3502128e8373a014fb8622feda9f97f8291617a14": {
        "chars": ("borscht", "purple_star", "kamaz", "meimei"),
        "all_unlocked": True,
        "label": "Full Developer Edition",
    },
}

_REG_PATH = r"Software\Blip"


def _b64u_decode(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


def get_hwid() -> str:
    """Return a stable 64-char SHA-256 hardware identifier for the current Windows machine."""
    parts: list[str] = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                val, _ = winreg.QueryValueEx(k, "MachineGuid")
                if val:
                    parts.append(str(val))
        except Exception:
            pass
    parts.append(os.environ.get("COMPUTERNAME", "") or os.environ.get("HOSTNAME", "") or "blip-host")
    parts.append(os.environ.get("USERNAME", "") or os.environ.get("USER", "") or "user")
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_ed25519_license(
    license_obj: dict,
    expected_hwid: str | None = None,
    public_key_b64u: str = ED25519_PUBLIC_KEY_B64U,
) -> tuple[bool, dict]:
    """Verify an Ed25519-signed Blip license object."""
    if not isinstance(license_obj, dict):
        return False, {}
    payload_str = license_obj.get("payload")
    sig_b64u = license_obj.get("signature")
    if not isinstance(payload_str, str) or not isinstance(sig_b64u, str):
        return False, {}
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        pub = Ed25519PublicKey.from_public_bytes(_b64u_decode(public_key_b64u))
        pub.verify(_b64u_decode(sig_b64u), payload_str.encode("utf-8"))
        payload = json.loads(payload_str)
    except Exception:
        return False, {}

    if not isinstance(payload, dict):
        return False, {}
    if payload.get("product") not in (APP_PRODUCT_NAME, "Blip Island"):
        return False, {}
    if str(payload.get("tier", "")).lower() != "pro":
        return False, {}
    if expected_hwid and not hmac.compare_digest(str(payload.get("hwid", "")), expected_hwid):
        return False, {}
    return True, payload


def _reg_read(name: str) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            val, _ = winreg.QueryValueEx(k, name)
            return str(val or "")
    except Exception:
        return ""


def _reg_write(name: str, value: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as k:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    except Exception:
        pass


def format_trial_remaining(days_left: float) -> str:
    """Format remaining trial duration as 'X дн. Y ч.' instead of fractional days."""
    total_hours = max(0, int(round(max(0.0, float(days_left)) * 24.0)))
    days, hours = divmod(total_hours, 24)
    return f"{days} дн. {hours} ч."


def _resolve_doh_ip(hostname: str) -> str | None:
    import httpx

    for doh_url in (
        f"https://dns.google/resolve?name={hostname}&type=A",
        f"https://1.1.1.1/dns-query?name={hostname}&type=A",
    ):
        for trust_env in (True, False):
            try:
                r = httpx.get(
                    doh_url,
                    headers={"accept": "application/dns-json"},
                    timeout=5.0,
                    trust_env=trust_env,
                )
                if r.status_code == 200:
                    for ans in r.json().get("Answer", []):
                        ip = str(ans.get("data", "")).strip()
                        if ip and ip.count(".") == 3:
                            return ip
            except Exception:
                continue
    return None


def _worker_request(
    method: str,
    full_url: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: float = 12.0,
) -> tuple[int, dict]:
    """Perform HTTP request to Cloudflare Worker with proxy-bypass and DoH fallback."""
    import http.client
    import socket
    import ssl
    from urllib.parse import urlencode, urlparse

    import httpx

    last_exc: Exception | None = None
    for trust_env in (True, False):
        try:
            with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
                resp = client.request(method, full_url, json=json_body, params=params)
                try:
                    data = resp.json()
                except Exception:
                    data = {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:120].strip()}"}
                return resp.status_code, data
        except Exception as exc:
            last_exc = exc

    # DNS fallback via DoH (fixes ISP/VPN getaddrinfo failures on *.workers.dev)
    parsed = urlparse(full_url)
    host = parsed.hostname or ""
    ip = _resolve_doh_ip(host) if host else None
    if ip:
        path = parsed.path or "/"
        if params:
            path = f"{path}?{urlencode(params)}"
        body_bytes = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {"Host": host, "User-Agent": "BlipIsland/1.0"}
        if body_bytes is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body_bytes))

        ctx = ssl.create_default_context()
        raw_sock = socket.create_connection((ip, 443), timeout=timeout)
        tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
        conn = http.client.HTTPSConnection(host, timeout=timeout, context=ctx)
        conn.sock = tls_sock
        try:
            conn.request(method, path, body=body_bytes, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw)
        finally:
            conn.close()

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Worker request failed")


@dataclass
class LicenseStatus:
    tier: str  # "PRO" | "TRIAL" | "FREE"
    is_pro: bool
    is_permanent_pro: bool
    trial_days_left: float
    unlocked_dev_chars: set[str] = field(default_factory=set)
    all_dev_unlocked: bool = False
    badge_text: str = "FREE"
    source: str = "trial"
    is_master: bool = False
    payment_blocked: bool = False
    forced_free: bool = False
    revoked_reason: str = ""
    blocked_features: tuple[str, ...] = ()


class LicenseManager:
    """Manages Blip PRO licenses, 3-day trial, DPAPI/registry persistence, and feature gates."""

    def __init__(self, project_dir: Path | None = None, appdata_dir: Path | None = None) -> None:
        self.project_dir = project_dir or Path(__file__).resolve().parent.parent.parent
        local_app = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        if appdata_dir is not None:
            self.appdata_dir = appdata_dir
        elif os.environ.get("BLIP_DEV_DIR"):
            self.appdata_dir = Path(os.environ["BLIP_DEV_DIR"])
        elif os.environ.get("BLIP_PROFILE", "").strip().lower() == "dev":
            self.appdata_dir = local_app / f"{APP_PRODUCT_NAME}Dev"
        else:
            self.appdata_dir = local_app / APP_PRODUCT_NAME
        self.appdata_dir.mkdir(parents=True, exist_ok=True)
        self.hwid = get_hwid()
        self._cached_status: LicenseStatus | None = None
        self._policies_synced = False

    @property
    def project_license_path(self) -> Path:
        return self.project_dir / "blip.lic"

    @property
    def appdata_license_path(self) -> Path:
        return self.appdata_dir / "blip.lic"

    @property
    def _trial_file(self) -> Path:
        return self.appdata_dir / "trial.dat"

    @property
    def _dev_file(self) -> Path:
        return self.appdata_dir / "dev_unlock.dat"

    @property
    def _policies_cache_file(self) -> Path:
        return self.appdata_dir / "code_policies_cache.json"

    @staticmethod
    def hash_dev_code(code: str) -> str:
        return hashlib.sha256((_DEV_SALT + str(code or "").strip()).encode("utf-8")).hexdigest()

    def load_code_policies(self) -> dict[str, dict]:
        """Load per-code access policies from repo file or local cache."""
        for p in (self._policies_cache_file, self.project_dir / "code_policies.json"):
            if p.exists():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    pols = raw.get("policies") if isinstance(raw, dict) else None
                    if isinstance(pols, dict):
                        return {str(k): dict(v) for k, v in pols.items() if isinstance(v, dict)}
                except Exception:
                    pass
        return {}

    def sync_code_policies_bg(self) -> None:
        """Fetch latest code_policies.json from GitHub in background without blocking UI."""
        if self._policies_synced:
            return
        self._policies_synced = True
        import threading

        def _worker() -> None:
            try:
                import httpx

                with httpx.Client(timeout=6.0, follow_redirects=True) as client:
                    r = client.get(CODE_POLICIES_URL, headers={"Cache-Control": "no-cache"})
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict) and isinstance(data.get("policies"), dict):
                            self._policies_cache_file.write_text(
                                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                            )
                            self.invalidate_cache()
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def set_code_policy(
        self,
        code_or_hash: str,
        *,
        mode: str = "active",
        block_payment: bool = False,
        blocked_features: list[str] | tuple[str, ...] = (),
        label: str = "",
        push_github: bool = True,
    ) -> tuple[bool, str]:
        """Set access policy for an existing developer code (Master key BORhBaRsMEYY673514 only)."""
        st = self.get_status()
        if not st.is_master:
            return False, "Доступно только по мастер-коду BORh"
        raw = str(code_or_hash or "").strip()
        if not raw:
            return False, "Укажите код или его SHA-256 хеш"
        h = raw.lower() if len(raw) == 64 and all(c in "0123456789abcdef" for c in raw.lower()) else self.hash_dev_code(raw)
        if h == _MASTER_BORH_HASH:
            return False, "Мастер-код нельзя заблокировать"
        policies = self.load_code_policies()
        if mode == "active" and not block_payment and not blocked_features:
            policies.pop(h, None)
        else:
            policies[h] = {
                "mode": mode,
                "block_payment": bool(block_payment),
                "blocked_features": list(blocked_features),
                "label": label or raw[:12],
                "updated_at": int(time.time()),
            }
        payload = {"policies": policies}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        self._policies_cache_file.write_text(text, encoding="utf-8")
        repo_file = self.project_dir / "code_policies.json"
        if (self.project_dir / ".git").exists() or repo_file.exists():
            try:
                repo_file.write_text(text + "\n", encoding="utf-8")
            except Exception:
                pass

        gh_msg = "Локально сохранено"
        if push_github:
            import shutil
            import subprocess

            no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            gh_bin = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
            pushed = False
            if gh_bin and Path(gh_bin).exists():
                try:
                    get_res = subprocess.run(
                        [gh_bin, "api", "repos/borissinugin-dotcom/blip-island/contents/code_policies.json"],
                        capture_output=True,
                        text=True,
                        creationflags=no_win,
                        timeout=12,
                    )
                    sha = ""
                    if get_res.returncode == 0:
                        sha = str(json.loads(get_res.stdout).get("sha") or "")
                    b64_content = base64.b64encode((text + "\n").encode("utf-8")).decode("ascii")
                    cmd = [
                        gh_bin,
                        "api",
                        "-X",
                        "PUT",
                        "repos/borissinugin-dotcom/blip-island/contents/code_policies.json",
                        "-f",
                        "message=Update code_policies.json",
                        "-f",
                        f"content={b64_content}",
                    ]
                    if sha:
                        cmd.extend(["-f", f"sha={sha}"])
                    put_res = subprocess.run(cmd, capture_output=True, text=True, creationflags=no_win, timeout=15)
                    if put_res.returncode == 0:
                        pushed = True
                        gh_msg = "Применено и обновлено на GitHub!"
                    else:
                        gh_msg = f"Ошибка GitHub API: {(put_res.stderr or put_res.stdout).strip()[:80]}"
                except Exception as exc:
                    gh_msg = f"Ошибка GitHub API: {exc}"
            if not pushed and (self.project_dir / ".git").exists():
                try:
                    subprocess.run(["git", "add", "code_policies.json"], cwd=str(self.project_dir), check=True, capture_output=True, creationflags=no_win)
                    subprocess.run(
                        ["git", "-c", "user.name=Borh", "-c", "user.email=borh@local", "commit", "-m", "Update code_policies.json"],
                        cwd=str(self.project_dir),
                        check=False,
                        capture_output=True,
                        creationflags=no_win,
                    )
                    pr = subprocess.run(["git", "push", "origin", "main"], cwd=str(self.project_dir), capture_output=True, text=True, creationflags=no_win, timeout=20)
                    if pr.returncode == 0:
                        pushed = True
                        gh_msg = "Применено и обновлено на GitHub!"
                except Exception:
                    pass
        self.invalidate_cache()
        return True, gh_msg

    def invalidate_cache(self) -> None:
        self._cached_status = None

    def _read_dpapi_json(self, file_path: Path, reg_name: str) -> dict:
        for raw in (
            _reg_read(reg_name),
            file_path.read_text(encoding="utf-8") if file_path.exists() else "",
        ):
            if not raw:
                continue
            try:
                dec = decrypt_secret(raw.strip())
                if dec:
                    data = json.loads(dec)
                    if isinstance(data, dict):
                        return data
            except Exception:
                continue
        return {}

    def _write_dpapi_json(self, file_path: Path, reg_name: str, data: dict) -> None:
        token = encrypt_secret(json.dumps(data, ensure_ascii=False))
        _reg_write(reg_name, token)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(token, encoding="utf-8")
        except Exception:
            pass

    def _load_paid_license(self) -> dict | None:
        candidates: list[str] = []
        for path in (self.project_license_path, self.appdata_license_path):
            if path.exists():
                try:
                    candidates.append(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        reg_tok = _reg_read("License")
        if reg_tok:
            try:
                dec = decrypt_secret(reg_tok)
                if dec:
                    candidates.append(dec)
            except Exception:
                pass

        for raw in candidates:
            try:
                obj = json.loads(raw)
                ok, payload = verify_ed25519_license(obj, expected_hwid=self.hwid)
                if ok:
                    # Sync across all storage locations so updates/reinstalls keep PRO
                    self._persist_license_obj(obj)
                    return payload
            except Exception:
                continue
        return None

    def _persist_license_obj(self, obj: dict) -> None:
        raw_json = json.dumps(obj, ensure_ascii=False)
        for path in (self.project_license_path, self.appdata_license_path):
            try:
                if not path.exists() or path.read_text(encoding="utf-8") != raw_json:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(raw_json, encoding="utf-8")
            except Exception:
                pass
        try:
            _reg_write("License", encrypt_secret(raw_json))
        except Exception:
            pass

    def _load_dev_unlock(self) -> tuple[bool, set[str], bool, bool, dict]:
        data: dict = {}
        if self._dev_file.exists():
            try:
                dec = decrypt_secret(self._dev_file.read_text(encoding="utf-8").strip())
                if dec:
                    parsed = json.loads(dec)
                    if isinstance(parsed, dict):
                        data = parsed
            except Exception:
                data = {}
        if not data or not hmac.compare_digest(str(data.get("hwid", "")), self.hwid):
            return False, set(), False, False, {}
        used_codes = [str(x) for x in data.get("used_codes", [])]
        is_master = _MASTER_BORH_HASH in used_codes
        if not is_master and used_codes:
            policies = self.load_code_policies()
            for ch in used_codes:
                pol = policies.get(ch)
                if pol and pol.get("mode") in ("revoked", "forced_free"):
                    return False, set(), False, False, pol
                if pol and pol.get("blocked_features"):
                    chars = {str(c) for c in data.get("chars", []) if str(c) in DEV_EXCLUSIVE_CHARACTER_IDS}
                    return bool(data.get("pro", False)), chars, bool(data.get("all_unlocked", False)), False, pol
        chars = {str(c) for c in data.get("chars", []) if str(c) in DEV_EXCLUSIVE_CHARACTER_IDS}
        all_u = bool(data.get("all_unlocked", False))
        return bool(data.get("pro", False)), chars, all_u, is_master, {}

    def _check_trial(self) -> tuple[bool, float]:
        now = time.time()
        data = self._read_dpapi_json(self._trial_file, "TrialState")
        if not data or not hmac.compare_digest(str(data.get("hwid", "")), self.hwid):
            state = {"hwid": self.hwid, "started_at": now, "last_seen_at": now, "tampered": False}
            self._write_dpapi_json(self._trial_file, "TrialState", state)
            return True, 3.0

        started = float(data.get("started_at", now))
        last_seen = float(data.get("last_seen_at", started))
        tampered = bool(data.get("tampered", False))
        # Anti-clock-rollback guard (5 min tolerance)
        if now + 300.0 < last_seen:
            tampered = True
        if now > last_seen:
            data["last_seen_at"] = now
        data["tampered"] = tampered
        self._write_dpapi_json(self._trial_file, "TrialState", data)

        if tampered:
            return False, 0.0
        remaining = TRIAL_DURATION_SECONDS - max(0.0, now - started)
        if remaining > 0.0:
            return True, remaining / 86400.0
        return False, 0.0

    def get_status(self, force_refresh: bool = False) -> LicenseStatus:
        self.sync_code_policies_bg()
        if self._cached_status is not None and not force_refresh:
            return self._cached_status

        dev_pro, dev_chars, all_dev, is_master, active_pol = self._load_dev_unlock()
        paid_payload = self._load_paid_license()
        pol_mode = str(active_pol.get("mode") or "")
        pay_blocked = bool(active_pol.get("block_payment", False)) and not is_master
        blocked_feats = tuple(str(x) for x in active_pol.get("blocked_features", ()) if x) if not is_master else ()

        if (pol_mode in ("revoked", "forced_free")) and not is_master:
            st = LicenseStatus(
                tier="FREE",
                is_pro=False,
                is_permanent_pro=False,
                trial_days_left=0.0,
                unlocked_dev_chars=set(),
                all_dev_unlocked=False,
                badge_text="FREE",
                source="revoked" if pol_mode == "revoked" else "forced_free",
                is_master=False,
                payment_blocked=pay_blocked,
                forced_free=True,
                revoked_reason="Код заблокирован разработчиком" if pol_mode == "revoked" else "Включён бесплатный режим",
                blocked_features=blocked_feats,
            )
        elif paid_payload is not None or dev_pro:
            st = LicenseStatus(
                tier="PRO",
                is_pro=True,
                is_permanent_pro=True,
                trial_days_left=0.0,
                unlocked_dev_chars=dev_chars,
                all_dev_unlocked=all_dev,
                badge_text="PRO",
                source="dev_code" if dev_pro else "license",
                is_master=is_master,
                payment_blocked=pay_blocked,
                blocked_features=blocked_feats,
            )
        else:
            trial_ok, days_left = self._check_trial()
            if trial_ok:
                st = LicenseStatus(
                    tier="TRIAL",
                    is_pro=True,
                    is_permanent_pro=False,
                    trial_days_left=days_left,
                    unlocked_dev_chars=dev_chars,
                    all_dev_unlocked=all_dev,
                    badge_text="PRO",
                    source="trial",
                    is_master=is_master,
                    payment_blocked=pay_blocked,
                    blocked_features=blocked_feats,
                )
            else:
                st = LicenseStatus(
                    tier="FREE",
                    is_pro=False,
                    is_permanent_pro=False,
                    trial_days_left=0.0,
                    unlocked_dev_chars=dev_chars,
                    all_dev_unlocked=all_dev,
                    badge_text="FREE",
                    source="expired",
                    is_master=is_master,
                    payment_blocked=pay_blocked,
                    blocked_features=blocked_feats,
                )
        self._cached_status = st
        return st

    def is_character_allowed(self, char_id: str) -> bool:
        st = self.get_status()
        if char_id in DEV_EXCLUSIVE_CHARACTER_IDS:
            return st.all_dev_unlocked or (char_id in st.unlocked_dev_chars)
        if st.is_pro or st.all_dev_unlocked:
            return True
        return char_id in FREE_CHARACTER_IDS

    def is_feature_allowed(self, feature: str) -> bool:
        st = self.get_status()
        if feature in st.blocked_features and not st.is_master:
            return False
        if st.is_pro or st.all_dev_unlocked:
            return True
        return feature not in ("glass", "realtime_weather", "weather", "game_stats", "timer", "cursor_shield")

    def save_paid_license(self, license_obj: dict) -> tuple[bool, str]:
        ok, _ = verify_ed25519_license(license_obj, expected_hwid=self.hwid)
        if not ok:
            return False, "Недействительная подпись лицензии или чужой ПК (HWID)"
        self._persist_license_obj(license_obj)
        self.invalidate_cache()
        return True, "Подписка Blip PRO активирована!"

    def import_license_file(self, path: str | Path) -> tuple[bool, str]:
        try:
            obj = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"Не удалось прочитать файл лицензии: {exc}"
        return self.save_paid_license(obj)

    def activate_code(self, raw_code: str, worker_url: str = WORKER_BASE_URL) -> tuple[bool, str]:
        code = str(raw_code or "").strip()
        if not code:
            return False, "Введите код активации"

        # 1. Offline single-use check against salted SHA-256 developer code hashes
        digest = hashlib.sha256((_DEV_SALT + code).encode("utf-8")).hexdigest()
        dev_spec = _DEV_CODE_HASHES.get(digest)
        if dev_spec is not None:
            pol = self.load_code_policies().get(digest)
            if pol and pol.get("mode") == "revoked" and digest != _MASTER_BORH_HASH:
                return False, "Этот код разработчика заблокирован"
            dev_data = self._read_dpapi_json(self._dev_file, "DevUnlock") or {}
            used_codes = [str(x) for x in dev_data.get("used_codes", [])] if hmac.compare_digest(str(dev_data.get("hwid", "")), self.hwid) else []
            if digest in used_codes:
                return False, "Этот код разработчика уже был использован!"
            _, existing_chars, existing_all, _, _ = self._load_dev_unlock()
            new_chars = sorted(existing_chars | set(dev_spec["chars"]))  # type: ignore[arg-type]
            all_u = existing_all or bool(dev_spec["all_unlocked"])
            used_codes.append(digest)
            self._write_dpapi_json(
                self._dev_file,
                "DevUnlock",
                {
                    "hwid": self.hwid,
                    "pro": True,
                    "chars": new_chars,
                    "all_unlocked": all_u,
                    "used_codes": used_codes,
                },
            )
            self.invalidate_cache()
            return True, f"Активировано: {dev_spec['label']}"

        # 2. Online single-use code activation via Cloudflare Worker
        try:
            status_code, data = _worker_request(
                "POST",
                f"{worker_url.rstrip('/')}/code/activate",
                json_body={"code": code.upper(), "hwid": self.hwid},
                timeout=12.0,
            )
            if status_code == 200 and data.get("ok") and data.get("license"):
                return self.save_paid_license(data["license"])
            err = data.get("error", f"HTTP {status_code}")
            if err == "code_already_used":
                return False, "Этот код уже использован на другом ПК"
            if err == "invalid_code":
                return False, "Код не найден"
            return False, f"Ошибка активации: {err}"
        except Exception as exc:
            return False, f"Нет связи с сервером активации: {exc}"

    def create_payment_order(self, worker_url: str = WORKER_BASE_URL) -> tuple[bool, dict]:
        try:
            status_code, data = _worker_request(
                "POST",
                f"{worker_url.rstrip('/')}/order/create",
                json_body={"hwid": self.hwid, "tier": "pro", "plan": "3m", "duration_days": 90},
                timeout=12.0,
            )
            if status_code == 200 and data.get("ok"):
                return True, data
            return False, {"error": data.get("error", f"HTTP {status_code}")}
        except Exception as exc:
            return False, {"error": str(exc)}

    def poll_payment_order(
        self, order_id: str, order_token: str = "", worker_url: str = WORKER_BASE_URL
    ) -> tuple[str, str]:
        try:
            q_params = {"order_id": order_id, "hwid": self.hwid}
            if order_token:
                q_params["order_token"] = order_token
            status_code, data = _worker_request(
                "GET",
                f"{worker_url.rstrip('/')}/order/status",
                params=q_params,
                timeout=10.0,
            )
            if status_code == 200 and data.get("status") == "paid" and data.get("license"):
                ok, msg = self.save_paid_license(data["license"])
                return ("paid" if ok else "error"), msg
            return str(data.get("status", "pending")), ""
        except Exception as exc:
            return "error", str(exc)


_DEFAULT_LICENSE_MGR: LicenseManager | None = None


def get_license_manager() -> LicenseManager:
    global _DEFAULT_LICENSE_MGR
    if _DEFAULT_LICENSE_MGR is None:
        _DEFAULT_LICENSE_MGR = LicenseManager()
    return _DEFAULT_LICENSE_MGR
