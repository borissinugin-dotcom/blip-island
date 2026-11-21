"""Dark/Light/System palette management and modern QSS stylesheets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.win32_api import is_windows_dark_theme


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) != 6:
        return (59, 130, 246)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return (59, 130, 246)


def blend_hex(hex_a: str, hex_b: str, t: float) -> str:
    """Linearly interpolate between two #RRGGBB hex colors (t in 0..1)."""
    k = max(0.0, min(1.0, float(t)))
    r1, g1, b1 = _hex_to_rgb(hex_a)
    r2, g2, b2 = _hex_to_rgb(hex_b)
    r = round(r1 + (r2 - r1) * k)
    g = round(g1 + (g2 - g1) * k)
    b = round(b1 + (b2 - b1) * k)
    return f"#{r:02X}{g:02X}{b:02X}"


def contrast_fg(hex_color: str) -> str:
    """Return dark ink or crisp white for maximum readability over hex_color."""
    r, g, b = _hex_to_rgb(hex_color)
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return "#0B0D13" if lum > 0.62 else "#FFFFFF"


def _hex_rgba(hx: str, alpha: int = 200) -> str:
    h = (hx or "").strip().lstrip("#")
    if len(h) == 8:
        h = h[2:]
    if len(h) == 6:
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            r, g, b = (20, 24, 34)
    else:
        r, g, b = (20, 24, 34)
    a = max(0, min(255, int(alpha)))
    return f"rgba({r}, {g}, {b}, {a})"


_DARK_TIME_STOPS: list[tuple[float, str]] = [
    (0.0, "#2563EB"),   # Midnight deep sapphire blue
    (3.5, "#3B82F6"),   # Night electric blue
    (6.0, "#FB923C"),   # Sunrise warm coral-amber
    (8.5, "#0EA5E9"),   # Morning crisp azure sky
    (12.5, "#38BDF8"),  # Midday vibrant sky cyan (never flat white)
    (16.0, "#0284C7"),  # Afternoon cerulean
    (18.5, "#F97316"),  # Sunset glowing amber-orange
    (20.5, "#EC4899"),  # Twilight dusk rose-magenta
    (22.0, "#3B82F6"),  # Evening deep sapphire blue
    (24.0, "#2563EB"),  # Wrap midnight
]

_LIGHT_TIME_STOPS: list[tuple[float, str]] = [
    (0.0, "#1D4ED8"),
    (6.0, "#EA580C"),
    (10.0, "#0284C7"),
    (16.0, "#0369A1"),
    (18.5, "#C2410C"),
    (21.0, "#1D4ED8"),
    (24.0, "#1D4ED8"),
]


def resolve_time_accent(
    now: datetime | None = None,
    *,
    is_dark: bool = True,
    weather_symbol: str = "",
    preview_mode: str = "auto",
    lat: float | None = None,
    lon: float | None = None,
    sky_mode: str = "auto",
    weather_mode: str = "auto",
    moon_mode: str = "auto",
) -> str:
    """Compute non-white atmospheric accent harmonized with time of day, city sun elevation, and weather."""
    from .atmosphere import resolve_scene_state

    mode = (preview_mode or "auto").strip().lower()
    if mode != "auto" or (sky_mode and sky_mode != "auto") or (weather_mode and weather_mode != "auto") or (moon_mode and moon_mode != "auto"):
        seg, wkind, _ = resolve_scene_state(
            mode,
            weather_symbol,
            now,
            lat=lat,
            lon=lon,
            sky_mode=sky_mode,
            weather_mode=weather_mode,
            moon_mode=moon_mode,
        )
        if wkind in ("rain", "thunder", "lightning"):
            return "#06B6D4"
        if wkind == "snow":
            return "#22D3EE"
        if seg == "sunrise":
            return "#F59E0B"
        if seg == "sunset":
            return "#F97316"
        if seg == "night":
            return "#3B82F6"
        return "#38BDF8" if wkind == "clear" else "#0EA5E9"

    dt = now or datetime.now()
    hour_f = (dt.hour % 24) + (dt.minute / 60.0) + (dt.second / 3600.0)
    stops = _DARK_TIME_STOPS if is_dark else _LIGHT_TIME_STOPS
    base_c = stops[0][1]
    for i in range(len(stops) - 1):
        h0, c0 = stops[i]
        h1, c1 = stops[i + 1]
        if h0 <= hour_f <= h1:
            span = max(1e-5, h1 - h0)
            base_c = blend_hex(c0, c1, (hour_f - h0) / span)
            break

    has_coords = lat is not None and lon is not None and not (abs(lat) < 1e-6 and abs(lon) < 1e-6)
    seg, wkind, _ = resolve_scene_state(
        "auto",
        weather_symbol,
        dt,
        lat=lat,
        lon=lon,
        sky_mode=sky_mode,
        weather_mode=weather_mode,
        moon_mode=moon_mode,
    )
    if has_coords:
        if seg == "sunrise":
            base_c = blend_hex(base_c, "#F59E0B", 0.55)
        elif seg == "sunset":
            base_c = blend_hex(base_c, "#F97316", 0.55)
        elif seg == "night":
            base_c = blend_hex(base_c, "#2563EB", 0.50)

    if wkind in ("rain", "thunder", "lightning"):
        return blend_hex(base_c, "#06B6D4", 0.65)
    if wkind == "snow":
        return blend_hex(base_c, "#22D3EE", 0.55)
    if wkind in ("cloudy", "overcast", "fog"):
        return blend_hex(base_c, "#38BDF8", 0.40) if weather_symbol else base_c
    return base_c


@dataclass(frozen=True)
class ThemePalette:
    is_dark: bool
    bg_island: str
    bg_card: str
    bg_surface: str
    bg_input: str
    bg_hover: str
    border: str
    text_primary: str
    text_secondary: str
    accent: str
    danger: str
    success: str
    warning: str


DARK_PALETTE = ThemePalette(
    is_dark=True,
    bg_island="#1F1B17",
    bg_card="#2B251F",
    bg_surface="#362F27",
    bg_input="#241F1A",
    bg_hover="#433A30",
    border="#6E5F4E",
    text_primary="#F5EDE0",
    text_secondary="#CFC2AE",
    accent="#D6B58B",
    danger="#E06C65",
    success="#8AB88A",
    warning="#E2B26F",
)

LIGHT_PALETTE = ThemePalette(
    is_dark=False,
    bg_island="#F4ECE0",
    bg_card="#FCF7EF",
    bg_surface="#ECE2D3",
    bg_input="#FDF9F2",
    bg_hover="#E2D6C3",
    border="#CBBCA7",
    text_primary="#2C241D",
    text_secondary="#746656",
    accent="#B88B58",
    danger="#C94F4A",
    success="#5F8D64",
    warning="#C4833B",
)


def resolve_palette(
    theme_mode: str,
    accent_override: str = "",
    now: datetime | None = None,
    *,
    music_tint: bool = False,
    color_tint: bool = False,
    weather_symbol: str = "",
    preview_mode: str = "auto",
    lat: float | None = None,
    lon: float | None = None,
    sky_mode: str = "auto",
    weather_mode: str = "auto",
    moon_mode: str = "auto",
) -> ThemePalette:
    """Resolve palette from 'dark' | 'light' | 'system' with circadian + weather mood or album/custom color tint."""
    from .atmosphere import resolve_scene_state

    mode = (theme_mode or "dark").lower()
    use_dark = is_windows_dark_theme() if mode == "system" else (mode != "light")
    base = DARK_PALETTE if use_dark else LIGHT_PALETTE
    has_override = bool(accent_override and accent_override.startswith("#") and len(accent_override) == 7)
    if has_override:
        eff_accent = accent_override
        if music_tint or color_tint:
            return ThemePalette(
                is_dark=base.is_dark,
                bg_island=blend_hex(base.bg_island, eff_accent, (0.36 if music_tint else 0.24) if use_dark else 0.14),
                bg_card=blend_hex(base.bg_card, eff_accent, (0.24 if music_tint else 0.16) if use_dark else 0.10),
                bg_surface=blend_hex(base.bg_surface, eff_accent, (0.32 if music_tint else 0.22) if use_dark else 0.16),
                bg_input=blend_hex(base.bg_input, eff_accent, (0.18 if music_tint else 0.14) if use_dark else 0.08),
                bg_hover=blend_hex(base.bg_hover, eff_accent, (0.40 if music_tint else 0.28) if use_dark else 0.20),
                border=blend_hex(base.border, eff_accent, (0.72 if music_tint else 0.45) if use_dark else 0.45),
                text_primary=base.text_primary,
                text_secondary=base.text_secondary,
                accent=eff_accent,
                danger=base.danger,
                success=base.success,
                warning=base.warning,
            )
        return ThemePalette(**{**base.__dict__, "accent": eff_accent})

    eff_accent = resolve_time_accent(
        now=now,
        is_dark=use_dark,
        weather_symbol=weather_symbol,
        preview_mode=preview_mode,
        lat=lat,
        lon=lon,
        sky_mode=sky_mode,
        weather_mode=weather_mode,
        moon_mode=moon_mode,
    )
    seg, wkind, _ = resolve_scene_state(
        preview_mode,
        weather_symbol,
        now,
        lat=lat,
        lon=lon,
        sky_mode=sky_mode,
        weather_mode=weather_mode,
        moon_mode=moon_mode,
    )
    if use_dark:
        sky_bg = "#0C0E14" if seg == "day" else ("#140E12" if seg in ("sunrise", "sunset") else "#08090D")
        if wkind in ("rain", "thunder", "lightning", "fog"):
            sky_bg = "#0A0C12"
        pastel_surface = blend_hex("#161922", eff_accent, 0.12)
        pastel_border = blend_hex("#383E4E", eff_accent, 0.24)
        return ThemePalette(
            is_dark=True,
            bg_island=blend_hex("#07080C", sky_bg, 0.55),
            bg_card=blend_hex("#11131A", sky_bg, 0.45),
            bg_surface=pastel_surface,
            bg_input=blend_hex("#0D0F16", sky_bg, 0.45),
            bg_hover=blend_hex("#1D222E", eff_accent, 0.18),
            border=pastel_border,
            text_primary=base.text_primary,
            text_secondary=base.text_secondary,
            accent=eff_accent,
            danger=base.danger,
            success=base.success,
            warning=base.warning,
        )
    return ThemePalette(**{**base.__dict__, "accent": eff_accent})


FIVE_PRESET_FONTS: list[tuple[str, str]] = [
    ("Segoe UI", "Segoe UI — Классический"),
    ("Bahnschrift", "Bahnschrift — Геометрия"),
    ("Trebuchet MS", "Trebuchet MS — Мягкий"),
    ("Georgia", "Georgia — С засечками"),
    ("Consolas", "Consolas — Техно-моно"),
]
_LOADED_CUSTOM_FONTS: dict[str, str] = {}


def apply_app_font(app, family: str = "Segoe UI", custom_path: str = "") -> str:
    """Register optional custom .ttf/.otf font file and set global QApplication font; returns active family."""
    from pathlib import Path
    from PyQt6.QtGui import QFont, QFontDatabase

    chosen = (family or "").strip() or "Segoe UI"
    cp = (custom_path or "").strip()
    if cp:
        cached = _LOADED_CUSTOM_FONTS.get(cp)
        if cached:
            chosen = cached
        elif Path(cp).is_file():
            fid = QFontDatabase.addApplicationFont(cp)
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    _LOADED_CUSTOM_FONTS[cp] = fams[0]
                    chosen = fams[0]
    if app is not None:
        f = QFont(chosen, 10)
        app.setFont(f)
    return chosen


def build_panel_qss(pal: ThemePalette, font_family: str = "", glass_mode: str = "off") -> str:
    """Return clean rounded Qt stylesheet supporting off (dark translucent), liquid, and frost glass modes."""
    accent_fg = contrast_fg(pal.accent)
    gm = str(glass_mode or "off").lower()
    if gm in ("liquid", "frost"):
        card_bg = "rgba(12, 16, 26, 26)" if pal.is_dark else "rgba(255, 255, 255, 36)"
        surf_bg = "rgba(14, 18, 30, 32)" if pal.is_dark else "rgba(255, 255, 255, 42)"
        inp_bg = "rgba(12, 16, 26, 75)" if pal.is_dark else _hex_rgba(pal.bg_input, 150)
        hover_bg = "rgba(255, 255, 255, 34)" if pal.is_dark else "rgba(255, 255, 255, 95)"
        border_col = "rgba(255, 255, 255, 0.16)" if pal.is_dark else _hex_rgba(pal.border, 150)
        checked_bg = _hex_rgba(pal.accent, 145)
    else:
        # Clean black/dark semi-transparent buttons & cards when glass is off
        card_bg = "rgba(8, 10, 15, 198)" if pal.is_dark else _hex_rgba(pal.bg_card, 196)
        surf_bg = "rgba(8, 10, 15, 222)" if pal.is_dark else _hex_rgba(pal.bg_surface, 192)
        inp_bg = "rgba(10, 12, 18, 220)" if pal.is_dark else _hex_rgba(pal.bg_input, 204)
        hover_bg = "rgba(24, 28, 40, 235)" if pal.is_dark else _hex_rgba(pal.bg_hover, 215)
        border_col = "rgba(255, 255, 255, 0.12)" if pal.is_dark else _hex_rgba(pal.border, 170)
        checked_bg = pal.accent
    fam = (font_family or "").strip().replace('"', "")
    font_rule = f'"{fam}", "Segoe UI Variable", "Segoe UI", sans-serif' if fam else '"Segoe UI Variable", "Segoe UI", sans-serif'
    return f"""
    QWidget {{
        color: {pal.text_primary};
        font-family: {font_rule};
        font-size: 12px;
        background: transparent;
    }}
    QFrame#Card {{
        background-color: {card_bg};
        border: 1px solid {border_col};
        border-radius: 22px;
    }}
    QFrame#Surface {{
        background-color: {surf_bg};
        border: 1px solid {border_col};
        border-radius: 18px;
    }}
    QLabel#Muted {{
        color: {pal.text_secondary};
        font-size: 11px;
    }}
    QLabel#Heading {{
        color: {pal.text_primary};
        font-size: 14px;
        font-weight: 600;
    }}
    QPushButton {{
        background-color: {surf_bg};
        color: {pal.text_primary};
        border: 1px solid {border_col};
        border-radius: 14px;
        min-height: 28px;
        padding: 0px 14px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {hover_bg};
        border-color: {pal.accent};
    }}
    QPushButton:checked, QPushButton#Primary {{
        background-color: {checked_bg};
        color: {accent_fg};
        border-color: {pal.accent};
    }}
    QPushButton#Danger {{
        background-color: {pal.danger};
        color: #FFFFFF;
        border-color: {pal.danger};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {inp_bg};
        color: {pal.text_primary};
        border: 1px solid {border_col};
        border-radius: 14px;
        min-height: 20px;
        padding: 4px 12px;
        selection-background-color: {pal.accent};
    }}
    QComboBox {{
        padding-right: 26px;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 24px;
        border: none;
        border-top-right-radius: 14px;
        border-bottom-right-radius: 14px;
        background: transparent;
    }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: padding;
        width: 16px;
        border: none;
        border-radius: 7px;
        background: transparent;
        margin-right: 3px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {hover_bg};
    }}
    QTextEdit, QPlainTextEdit {{
        background-color: {inp_bg};
        color: {pal.text_primary};
        border: 1px solid {border_col};
        border-radius: 16px;
        padding: 6px 10px;
        selection-background-color: {pal.accent};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {pal.accent};
    }}
    QComboBox QAbstractItemView {{
        background-color: {pal.bg_card};
        color: {pal.text_primary};
        border: 1px solid {pal.border};
        border-radius: 14px;
        padding: 4px;
        selection-background-color: {pal.accent};
        outline: none;
    }}
    QTabWidget::pane {{
        border: 1px solid {border_col};
        border-radius: 18px;
        background-color: {card_bg};
        padding: 6px;
        margin-top: 4px;
    }}
    QTabBar::tab {{
        background-color: {surf_bg};
        color: {pal.text_secondary};
        border: 1px solid {border_col};
        border-radius: 13px;
        min-height: 26px;
        padding: 2px 12px;
        margin-right: 5px;
        margin-bottom: 2px;
        font-weight: 500;
    }}
    QTabBar::tab:hover {{
        background-color: {hover_bg};
        color: {pal.text_primary};
    }}
    QTabBar::tab:selected {{
        background-color: {pal.accent};
        color: {accent_fg};
        border-color: {pal.accent};
        font-weight: 600;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 8px;
        border: 1px solid {border_col};
        background-color: {inp_bg};
    }}
    QCheckBox::indicator:hover {{
        border-color: {pal.accent};
    }}
    QCheckBox::indicator:checked {{
        background-color: {pal.accent};
        border-color: {pal.accent};
    }}
    QListWidget, QTreeWidget {{
        background-color: {inp_bg};
        color: {pal.text_primary};
        border: 1px solid {border_col};
        border-radius: 18px;
        padding: 5px;
        outline: none;
    }}
    QListWidget::item {{
        border-radius: 12px;
        padding: 6px 8px;
        margin: 1px 0;
    }}
    QListWidget::item:hover {{
        background-color: {pal.bg_hover};
    }}
    QListWidget::item:selected {{
        background-color: {pal.accent};
        color: {accent_fg};
    }}
    QSlider::groove:horizontal {{
        height: 4px;
        background: {pal.border};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {pal.accent};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 12px;
        height: 12px;
        margin: -4px 0;
        border-radius: 6px;
        background: #FFFFFF;
        border: 2px solid {pal.accent};
    }}
    QProgressBar {{
        background-color: {pal.bg_input};
        border: 1px solid {pal.border};
        border-radius: 5px;
        height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {pal.accent};
        border-radius: 4px;
    }}
    QScrollBar:vertical {{
        width: 6px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        background: {pal.border};
        border-radius: 3px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        height: 0px;
        background: transparent;
    }}
    QScrollBar:horizontal {{
        height: 6px;
        background: transparent;
    }}
    QScrollBar::handle:horizontal {{
        background: {pal.border};
        border-radius: 3px;
        min-width: 20px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        width: 0px;
        background: transparent;
    }}
    """
