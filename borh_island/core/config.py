"""Application configuration, paths, and provider presets."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from borh_island.core.security import decrypt_secret, encrypt_secret

APP_NAME = "Blip Island"
APP_ID = "BorhIsland"
DEFAULT_AGENT_PORT = 17483

AI_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "groq": {
        "name": "Groq",
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "stt_model": "whisper-large-v3-turbo",
        "supports_vision": True,
        "supports_stt": True,
        "key_url": "https://console.groq.com/keys",
        "note_ru": "Около 1000 запросов в день бесплатно; Whisper до 8ч речи. Из РФ нужен VPN.",
        "note_en": "~1000 free requests/day, no card; Whisper up to 8h speech/day.",
    },
    "gemini_local": {
        "name": "Gemini (Локальный ПК / CLI / шлюз)",
        "kind": "gemini_local",
        "base_url": "http://127.0.0.1:17483",
        "default_model": "gemini-2.5-flash",
        "stt_model": "",
        "supports_vision": False,
        "supports_stt": False,
        "key_url": "https://aistudio.google.com/apikey",
        "note_ru": "Чисто текстовая интеграция с локальным Gemini (CLI, локальный HTTP-мост или AI Studio без вызова функций).",
        "note_en": "Pure-text integration with local Gemini CLI, local bridge, or AI Studio without function calls.",
    },
    "gemini": {
        "name": "Google Gemini (API)",
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
        "stt_model": "gemini-2.5-flash",
        "supports_vision": True,
        "supports_stt": True,
        "key_url": "https://aistudio.google.com/apikey",
        "note_ru": "Официальный Google AI Studio API (работает также с GEMINI_API_KEY).",
        "note_en": "Free/paid tier in Google AI Studio (also reads GEMINI_API_KEY).",
    },
    "openrouter": {
        "name": "OpenRouter",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/auto",
        "stt_model": "",
        "supports_vision": True,
        "supports_stt": False,
        "key_url": "https://openrouter.ai/keys",
        "note_ru": "До 50 запросов в день на бесплатных моделях. Из РФ нужен VPN.",
        "note_en": "Up to 50 free requests/day on free models.",
    },
    "openai": {
        "name": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "stt_model": "whisper-1",
        "supports_vision": True,
        "supports_stt": True,
        "key_url": "https://platform.openai.com/api-keys",
        "note_ru": "Официальный OpenAI API.",
        "note_en": "Official OpenAI API.",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-sonnet-4-5",
        "stt_model": "",
        "supports_vision": True,
        "supports_stt": False,
        "key_url": "https://console.anthropic.com/settings/keys",
        "note_ru": "Прямой Anthropic Messages API.",
        "note_en": "Direct Anthropic Messages API.",
    },
    "deepseek": {
        "name": "DeepSeek",
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "stt_model": "",
        "supports_vision": False,
        "supports_stt": False,
        "key_url": "https://platform.deepseek.com/api_keys",
        "note_ru": "Платный API; отвечает из РФ без VPN.",
        "note_en": "Paid API.",
    },
    "polza": {
        "name": "Polza.ai",
        "kind": "openai",
        "base_url": "https://api.polza.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "stt_model": "",
        "supports_vision": True,
        "supports_stt": False,
        "key_url": "https://polza.ai",
        "note_ru": "Оплата в рублях, работает без VPN.",
        "note_en": "OpenAI-compatible gateway.",
    },
    "apinet": {
        "name": "APINET",
        "kind": "openai",
        "base_url": "https://api.apinet.ru/v1",
        "default_model": "gpt-4o-mini",
        "stt_model": "",
        "supports_vision": True,
        "supports_stt": False,
        "key_url": "",
        "note_ru": "Работает из РФ без VPN.",
        "note_en": "OpenAI-compatible gateway.",
    },
    "ollama": {
        "name": "Ollama / OmniRoute (Local)",
        "kind": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "llama3.2",
        "stt_model": "",
        "supports_vision": True,
        "supports_stt": False,
        "key_url": "",
        "note_ru": "Локальный шлюз или Ollama на вашем компьютере без ключа.",
        "note_en": "Local Ollama or OmniRoute gateway on this computer.",
    },
    "custom": {
        "name": "Custom OpenAI-Compatible",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "stt_model": "whisper-1",
        "supports_vision": True,
        "supports_stt": True,
        "key_url": "",
        "note_ru": "Любой OpenAI-совместимый адрес.",
        "note_en": "Any OpenAI-compatible endpoint.",
    },
}

DEFAULT_HOTKEYS: dict[str, str] = {
    "toggle_island": "Ctrl+Alt+I",
    "command_bar": "Alt+Space",
    "dictation": "Ctrl+Alt+D",
    "clipboard": "Ctrl+Alt+V",
    "screenshot_area": "Ctrl+Alt+S",
    "screenshot_full": "Ctrl+Alt+Shift+S",
    "ocr": "Ctrl+Alt+O",
    "qr_scan": "Ctrl+Alt+Q",
    "color_picker": "Ctrl+Alt+C",
    "screen_record": "Ctrl+Alt+R",
    "paste_plain": "Ctrl+Alt+Shift+V",
    "mute_mic": "Ctrl+Alt+M",
    "toggle_theme": "Ctrl+Alt+T",
    "toggle_through": "Ctrl+Alt+Shift+T",
    "settings": "Ctrl+Alt+P",
    "quit_app": "",
}


def get_app_data_dir(custom_dir: Path | None = None) -> Path:
    """Return the persistent user data directory and ensure subfolders exist."""
    if custom_dir is not None:
        base = Path(custom_dir)
    else:
        local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(local_app) / APP_ID
    for sub in ("", "screenshots", "recordings", "clipboard_images", "shelf", "backups"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class AppConfig:
    """User configuration persisted as JSON (with DPAPI-encrypted secrets)."""

    language: str = "ru"
    theme: str = "dark"  # dark | light | system
    scale: float = 1.0  # 0.8 .. 2.0
    collapsed_opacity: float = 0.94
    expanded_opacity: float = 0.96
    monitor_index: int = 0
    offset_x: int = 0
    auto_hide: bool = True
    auto_hide_excluded_apps: list[str] = field(default_factory=list)
    hide_in_fullscreen: bool = True
    click_through: bool = False
    exclude_from_capture: bool = False
    blue_light_filter: bool = False
    pill_mode: str = "track"  # compact | track | miniplayer
    liquid_drop: bool = True
    glass_mode: str = "liquid"  # off | liquid | frost
    pill_glass_mode: str = "auto"  # auto | off | liquid | frost
    notification_glass_mode: str = "auto"  # auto | off | liquid | frost
    refraction_quality: str = "ultra"  # low | medium | high | ultra
    refraction_bench_ms: float = 0.0
    target_fps: int = 120  # 240 | 120 | 60 | 30
    remove_power_limits: bool = True  # Disable Windows EcoQoS/PowerThrottling & boost UI priority for laptops
    edge_equalizer: bool = True
    show_lyrics_in_pill: bool = True
    show_weather_in_pill: bool = True
    show_game_stats_in_pill: bool = True
    show_game_cpu: bool = True
    show_game_ram: bool = True
    show_game_gpu: bool = True
    atmosphere_quality: int = 100
    ui_font_family: str = "Segoe UI"
    custom_font_path: str = ""
    dnd_until_ts: float = 0.0
    atmosphere_preview: str = "auto"  # auto | sunrise | day_clear | day_cloudy | day_rain | sunset | night_full_moon | night_new_moon | night_crescent
    atmosphere_sky_mode: str = "auto"  # auto | sunrise | day | sunset | night
    atmosphere_weather_mode: str = "auto"  # auto | clear | cloudy | overcast | rain | thunder | lightning | snow
    atmosphere_moon_mode: str = "auto"  # auto | new | waxing_crescent | first_quarter | waxing_gibbous | full | waning_gibbous | last_quarter | waning_crescent
    show_dev_text_tools: bool = False
    hide_pro_badge: bool = False
    island_appearance_mode: str = "weather"  # weather | music | color | image | video
    accent_color: str = ""
    music_last_accent: str = ""
    custom_bg_image_path: str = ""
    custom_bg_video_path: str = ""
    player_card_style: str = "mirror"  # mirror | fill | compact
    home_hidden_blocks: list[str] = field(default_factory=list)
    home_hidden_tools: list[str] = field(default_factory=list)
    character_id: str = "liquid"
    autostart: bool = False
    discord_rpc_enabled: bool = True
    discord_client_id: str = ""

    # Audio & Privacy
    output_device_id: str = "default"
    input_device_id: str = "default"
    audio_mixer_active_profile: str = "Стандартный"
    audio_mixer_profiles: dict[str, dict[str, dict[str, int | bool]]] = field(
        default_factory=lambda: {
            "Стандартный": {},
            "Игра + Discord": {
                "discord": {"volume": 100, "muted": False},
                "spotify": {"volume": 40, "muted": False},
                "yandexmusic": {"volume": 40, "muted": False},
                "chrome": {"volume": 30, "muted": False},
                "msedge": {"volume": 30, "muted": False},
                "browser": {"volume": 30, "muted": False},
                "telegram": {"volume": 65, "muted": False},
            },
            "Музыка и фокус": {
                "spotify": {"volume": 95, "muted": False},
                "yandexmusic": {"volume": 95, "muted": False},
                "discord": {"volume": 25, "muted": False},
                "telegram": {"volume": 20, "muted": True},
                "__system__": {"volume": 30, "muted": False},
            },
            "Ночной режим": {
                "spotify": {"volume": 35, "muted": False},
                "yandexmusic": {"volume": 35, "muted": False},
                "chrome": {"volume": 25, "muted": False},
                "msedge": {"volume": 25, "muted": False},
                "browser": {"volume": 25, "muted": False},
                "discord": {"volume": 45, "muted": False},
                "__system__": {"volume": 15, "muted": False},
            },
        }
    )
    clipboard_enabled: bool = True
    clipboard_max_items: int = 300
    clipboard_excluded_apps: list[str] = field(
        default_factory=lambda: ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe"]
    )
    vpn_ip_lookup: bool = True
    show_system_notifications: bool = True
    notification_excluded_apps: list[str] = field(default_factory=list)
    obs_status_enabled: bool = False
    obs_ws_port: int = 4455
    steam_downloads_enabled: bool = True

    # Weather
    weather_city: str = ""
    weather_lat: float | None = None
    weather_lon: float | None = None
    weather_rain_drip: bool = True

    # AI & Dictation
    ai_provider: str = "groq"
    ai_base_url: str = "https://api.groq.com/openai/v1"
    ai_model: str = "llama-3.3-70b-versatile"
    stt_provider: str = "groq"
    stt_base_url: str = "https://api.groq.com/openai/v1"
    stt_model: str = "whisper-large-v3-turbo"
    stt_autotype: bool = False
    chat_browser_mode: str = "auto"  # auto | webengine | edge | chrome | yandex | firefox | brave | opera | vivaldi | system
    chat_app_window_mode: bool = True
    encrypted_keys: dict[str, str] = field(default_factory=dict)

    # Capture & Upload
    screenshot_dir: str = ""
    recording_dir: str = ""
    recording_fps: int = 30
    recording_format: str = "mp4"  # mp4 | gif
    recording_audio_mode: str = "system"  # none | system | both
    upload_service: str = "none"  # none | imgbb | imgur

    # Agents & Hotkeys & Hot-Reload
    agent_server_enabled: bool = False
    agent_server_port: int = DEFAULT_AGENT_PORT
    hot_reload: bool = True
    hotkeys: dict[str, str] = field(default_factory=lambda: copy.deepcopy(DEFAULT_HOTKEYS))

    def get_secret(self, name: str) -> str:
        """Decrypt and return a stored API key or token."""
        token = self.encrypted_keys.get(name, "")
        if not token:
            return ""
        return decrypt_secret(token)

    def set_secret(self, name: str, value: str) -> None:
        """Encrypt via DPAPI and store an API key or token."""
        cleaned = value.strip()
        if not cleaned:
            self.encrypted_keys.pop(name, None)
        else:
            self.encrypted_keys[name] = encrypt_secret(cleaned)

    def effective_screenshot_dir(self, data_dir: Path) -> Path:
        if self.screenshot_dir and Path(self.screenshot_dir).exists():
            return Path(self.screenshot_dir)
        p = data_dir / "screenshots"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def effective_recording_dir(self, data_dir: Path) -> Path:
        if self.recording_dir and Path(self.recording_dir).exists():
            return Path(self.recording_dir)
        p = data_dir / "recordings"
        p.mkdir(parents=True, exist_ok=True)
        return p


class ConfigManager:
    """Thread-safe loader/saver for AppConfig."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = get_app_data_dir(data_dir)
        self.config_path = self.data_dir / "config.json"
        self.config = self.load()

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            cfg = AppConfig()
            self.save(cfg)
            return cfg
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return AppConfig()
        defaults = asdict(AppConfig())
        merged: dict[str, Any] = {}
        for k, default_val in defaults.items():
            if k in raw:
                if k == "audio_mixer_profiles" and isinstance(raw[k], dict) and raw[k]:
                    merged[k] = copy.deepcopy(raw[k])
                elif isinstance(default_val, dict) and isinstance(raw[k], dict):
                    merged[k] = {**default_val, **raw[k]}
                    if k == "hotkeys" and "toggle_theme" not in raw[k] and merged[k].get("toggle_through") == "Ctrl+Alt+T":
                        merged[k]["toggle_through"] = "Ctrl+Alt+Shift+T"
                else:
                    merged[k] = raw[k]
            else:
                merged[k] = default_val
        return AppConfig(**merged)

    def save(self, cfg: AppConfig | None = None) -> None:
        target = cfg or self.config
        self.config = target
        self.config_path.write_text(
            json.dumps(asdict(target), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
