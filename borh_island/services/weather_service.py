"""Weather service powered by OpenStreetMap Nominatim (geocoding) and MET Norway API."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

_USER_AGENT = "DynamicIslandBorhEdition/1.0 (https://localhost.local/borh-island)"


@dataclass
class GeoPlace:
    name: str
    lat: float
    lon: float


@dataclass
class HourlyForecast:
    time_local: str
    temp_c: float
    symbol: str
    precip_mm: float
    wind_ms: float


@dataclass
class WeatherReport:
    city: str
    temp_c: float
    feels_like_c: float
    humidity: float
    wind_ms: float
    symbol: str
    description_ru: str
    description_en: str
    precip_next_hour: bool = False
    precip_kind: str = ""  # rain | snow | ""
    hourly: list[HourlyForecast] = field(default_factory=list)
    updated_at: float = 0.0
    wind_deg: float = 0.0
    pressure_mmhg: float = 760.0
    uv_index: float = 1.0
    precip_mm: float = 0.0
    sunrise_str: str = "07:00"
    sunset_str: str = "18:00"


_SYMBOL_INFO: dict[str, tuple[str, str, str]] = {
    "clearsky": ("☀", "Ясно", "Clear sky"),
    "fair": ("🌤", "Малооблачно", "Fair"),
    "partlycloudy": ("⛅", "Переменная облачность", "Partly cloudy"),
    "cloudy": ("☁", "Облачно", "Cloudy"),
    "fog": ("🌫", "Туман", "Fog"),
    "lightrain": ("🌦", "Небольшой дождь", "Light rain"),
    "rain": ("🌧", "Дождь", "Rain"),
    "heavyrain": ("⛈", "Сильный дождь", "Heavy rain"),
    "rainshowers": ("🌦", "Ливень", "Rain showers"),
    "lightrainshowers": ("🌦", "Кратковременный дождь", "Light showers"),
    "heavyrainshowers": ("⛈", "Сильный ливень", "Heavy showers"),
    "sleet": ("🌨", "Мокрый снег", "Sleet"),
    "lightsnow": ("🌨", "Небольшой снег", "Light snow"),
    "snow": ("❄", "Снег", "Snow"),
    "heavysnow": ("❄", "Сильный снег", "Heavy snow"),
    "snowshowers": ("🌨", "Снегопад", "Snow showers"),
    "rainandthunder": ("⛈", "Дождь с грозой", "Rain and thunder"),
    "thunder": ("🌩", "Гроза", "Thunderstorm"),
}


def describe_symbol(code: str) -> tuple[str, str, str]:
    base = code.split("_")[0].lower()
    if base in _SYMBOL_INFO:
        return _SYMBOL_INFO[base]
    for k, val in _SYMBOL_INFO.items():
        if k in base:
            return val
    return ("🌡", code, code)


def _wind_chill_or_apparent(temp_c: float, wind_ms: float, humidity: float) -> float:
    if temp_c <= 10.0 and wind_ms > 1.3:
        kmh = wind_ms * 3.6
        return round(13.12 + 0.6215 * temp_c - 11.37 * (kmh ** 0.16) + 0.3965 * temp_c * (kmh ** 0.16), 1)
    e = (humidity / 100.0) * 6.105 * (2.71828 ** (17.27 * temp_c / (237.7 + temp_c)))
    return round(temp_c + 0.33 * e - 0.70 * wind_ms - 4.0, 1)


def _calc_sunrise_sunset(lat: float, lon: float) -> dict[str, str]:
    """Compute approximate local sunrise and sunset HH:MM from lat/lon and day of year."""
    import math
    now_local = datetime.now().astimezone()
    doy = now_local.timetuple().tm_yday
    tz_h = (now_local.utcoffset().total_seconds() if now_local.utcoffset() else 0.0) / 3600.0
    gamma = 2.0 * math.pi / 365.0 * (doy - 1)
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
    )
    eq_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    lat_r = math.radians(max(-66.0, min(66.0, float(lat))))
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))) - math.tan(lat_r) * math.tan(decl)
    cos_ha = max(-0.98, min(0.98, cos_ha))
    ha_deg = math.degrees(math.acos(cos_ha))
    solar_noon_min = 720.0 - 4.0 * float(lon) - eq_time + tz_h * 60.0
    sr_min = max(0, min(1439, int(round(solar_noon_min - 4.0 * ha_deg))))
    ss_min = max(0, min(1439, int(round(solar_noon_min + 4.0 * ha_deg))))
    return {
        "sunrise_str": f"{sr_min // 60:02d}:{sr_min % 60:02d}",
        "sunset_str": f"{ss_min // 60:02d}:{ss_min % 60:02d}",
    }


class WeatherService:
    """City geocoding and 24-hour forecast via Nominatim & api.met.no."""

    def __init__(self) -> None:
        self._last_report: WeatherReport | None = None
        self._last_key: str = ""

    def search_cities(self, query: str) -> list[GeoPlace]:
        q = query.strip()
        if len(q) < 2:
            return []
        try:
            with httpx.Client(timeout=8.0, headers={"User-Agent": _USER_AGENT}) as client:
                r = client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "limit": 6, "addressdetails": 1},
                )
                r.raise_for_status()
                out: list[GeoPlace] = []
                for item in r.json():
                    out.append(
                        GeoPlace(
                            name=str(item.get("display_name", q)).split(",")[0]
                            + ", "
                            + ", ".join(str(item.get("display_name", "")).split(",")[1:3]).strip(),
                            lat=float(item["lat"]),
                            lon=float(item["lon"]),
                        )
                    )
                return out
        except Exception:
            return []

    def fetch_weather(self, city: str, lat: float, lon: float, force: bool = False) -> WeatherReport | None:
        cache_key = f"{city}|{lat:.2f}|{lon:.2f}"
        if (
            not force
            and self._last_report is not None
            and self._last_key == cache_key
            and time.time() - self._last_report.updated_at < 900
        ):
            return self._last_report
        try:
            with httpx.Client(timeout=10.0, headers={"User-Agent": _USER_AGENT}) as client:
                r = client.get(
                    "https://api.met.no/weatherapi/locationforecast/2.0/compact",
                    params={"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"},
                )
                r.raise_for_status()
                data = r.json()
            series = data.get("properties", {}).get("timeseries", [])
            if not series:
                return None

            cur = series[0]["data"]
            inst = cur.get("instant", {}).get("details", {})
            next1 = cur.get("next_1_hours", {})
            symbol = next1.get("summary", {}).get("symbol_code", "cloudy")
            temp = float(inst.get("air_temperature", 0.0))
            hum = float(inst.get("relative_humidity", 50.0))
            wind = float(inst.get("wind_speed", 0.0))
            wind_deg = float(inst.get("wind_from_direction", 0.0) or 0.0)
            pressure_hpa = float(inst.get("air_pressure_at_sea_level", 1013.25) or 1013.25)
            pressure_mmhg = round(pressure_hpa * 0.750062, 0)
            uv_val = float(inst.get("ultraviolet_index_clear_sky", 1.0) or 1.0)
            precip1 = float(next1.get("details", {}).get("precipitation_amount", 0.0) or 0.0)
            _, desc_ru, desc_en = describe_symbol(symbol)

            sym_low = symbol.lower()
            precip_kind = ""
            if precip1 >= 0.1 or any(k in sym_low for k in ("rain", "snow", "sleet", "thunder")):
                precip_kind = "snow" if ("snow" in sym_low or "sleet" in sym_low) else "rain"

            hourly: list[HourlyForecast] = []
            for entry in series[:12]:
                ts = entry.get("time", "")
                local_hhmm = ts[11:16]
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
                    local_hhmm = dt.strftime("%H:%M")
                except Exception:
                    pass
                ed = entry.get("data", {})
                e_inst = ed.get("instant", {}).get("details", {})
                e_n1 = ed.get("next_1_hours", {})
                e_sym = e_n1.get("summary", {}).get("symbol_code") or ed.get("next_6_hours", {}).get("summary", {}).get("symbol_code", "cloudy")
                e_pr = float(e_n1.get("details", {}).get("precipitation_amount", 0.0) or 0.0)
                hourly.append(
                    HourlyForecast(
                        time_local=local_hhmm,
                        temp_c=float(e_inst.get("air_temperature", temp)),
                        symbol=e_sym,
                        precip_mm=e_pr,
                        wind_ms=float(e_inst.get("wind_speed", 0.0)),
                    )
                )

            report = WeatherReport(
                city=city,
                temp_c=temp,
                feels_like_c=_wind_chill_or_apparent(temp, wind, hum),
                humidity=hum,
                wind_ms=wind,
                symbol=symbol,
                description_ru=desc_ru,
                description_en=desc_en,
                precip_next_hour=bool(precip_kind),
                precip_kind=precip_kind,
                hourly=hourly,
                updated_at=time.time(),
                wind_deg=wind_deg,
                pressure_mmhg=pressure_mmhg,
                uv_index=uv_val,
                precip_mm=precip1,
                **_calc_sunrise_sunset(lat, lon),
            )
            self._last_report = report
            self._last_key = cache_key
            return report
        except Exception:
            return self._last_report

    def detect_city_by_ip(self) -> GeoPlace | None:
        """Best-effort approximate city lookup via ipwho.is when no manual city is set."""
        try:
            with httpx.Client(timeout=4.0, headers={"User-Agent": _USER_AGENT}) as client:
                r = client.get("https://ipwho.is/")
                r.raise_for_status()
                data = r.json()
            if not data or data.get("success") is False:
                return None
            city = (data.get("city") or data.get("region") or "").strip()
            lat = data.get("latitude")
            lon = data.get("longitude")
            if city and lat is not None and lon is not None:
                return GeoPlace(name=city, lat=float(lat), lon=float(lon))
        except Exception:
            return None
        return None
