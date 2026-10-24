"""Weather at the truck's pitches: Open-Meteo's daily forecast (free, no key) → one plain dict per pitch per day.

Returns data only: storing it is the middle's job (inputs never write MongoDB directly). Bad or missing data
raises fetch.FetchError with a plain-English message; a single missing value (Open-Meteo sends null) stays None.

    python -m backend.inputs.weather            # next 7 days at every pitch
"""
import argparse
from datetime import date, datetime, timezone
from typing import List, Optional

from backend.inputs import fetch
from backend.inputs.pitches import PITCHES, Pitch

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
MAX_DAYS = 16  # Open-Meteo's forecast horizon
DAILY = ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
         "precipitation_probability_max", "wind_speed_10m_max"]
WMO = {  # WMO weather codes as used by Open-Meteo
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def describe(code: Optional[int]) -> str:
    return "No data" if code is None else WMO.get(code, f"Unknown (code {code})")


def _num(value, name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise fetch.FetchError(f"Open-Meteo sent a non-number for {name}: {value!r}")
    return value


def forecast(pitch: Pitch = PITCHES[0], days: int = 7, timeout: float = fetch.TIMEOUT) -> List[dict]:
    """The next `days` days (today first, London dates) at one pitch."""
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}.")
    data = fetch.get_json(OPEN_METEO, {
        "latitude": pitch["lat"], "longitude": pitch["lon"], "daily": ",".join(DAILY), "forecast_days": days,
        "timezone": "Europe/London", "temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm",
    }, source="Open-Meteo", timeout=timeout)
    daily = data.get("daily")
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise fetch.FetchError("Open-Meteo answered without a daily forecast")
    n = len(daily["time"])
    for name in DAILY:
        if not isinstance(daily.get(name), list) or len(daily[name]) != n:
            raise fetch.FetchError(f"Open-Meteo's daily {name} is missing or doesn't match the dates")

    fetched_at = datetime.now(timezone.utc).isoformat()
    out = []
    for i, day in enumerate(daily["time"]):
        try:
            date.fromisoformat(day)
        except (TypeError, ValueError):
            raise fetch.FetchError(f"Open-Meteo sent a bad date: {day!r}") from None
        v = {name: _num(daily[name][i], name) for name in DAILY}
        code = None if v["weather_code"] is None else int(v["weather_code"])
        out.append({
            "pitch_id": pitch["id"], "date": day, "summary": describe(code), "weather_code": code,
            "temp_max_c": v["temperature_2m_max"], "temp_min_c": v["temperature_2m_min"],
            "precip_mm": v["precipitation_sum"], "precip_chance_pct": v["precipitation_probability_max"],
            "wind_max_kmh": v["wind_speed_10m_max"], "source": "open-meteo", "fetched_at": fetched_at,
        })
    return out


def forecast_all(days: int = 7) -> List[dict]:
    """forecast() for every pitch."""
    return [d for pitch in PITCHES for d in forecast(pitch, days)]


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Show the Open-Meteo forecast at GAIA's pitches.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        days = forecast_all(args.days)
    except fetch.FetchError as e:
        raise SystemExit(f"⚠️ {e}")
    for d in days:
        rain = "?" if d["precip_chance_pct"] is None else f"{d['precip_chance_pct']:.0f}%"
        print(f"{d['date']}  {d['pitch_id']:<16} {d['summary']:<22} {d['temp_min_c']}–{d['temp_max_c']}°C  rain {rain}")


if __name__ == "__main__":
    main()
