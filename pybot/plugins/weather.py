"""
Weather plugin — current conditions and forecast via Open-Meteo (no API key needed).
Geocoding via Nominatim/OpenStreetMap (also free, no key).
"""

from __future__ import annotations

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


@plugin.command(
    "weather",
    aliases=["w"],
    help="Show current weather for a location",
    usage="!weather [location | set <location>]",
)
async def cmd_weather(bot: object, trigger: Trigger) -> None:
    from pybot.core.database import get_plugin_setting, get_session, set_plugin_setting

    # .weather set <location>
    if trigger.args and trigger.args[0].lower() == "set":
        if len(trigger.args) < 2:
            await bot.reply(trigger, "Usage: !weather set <location>")  # type: ignore[attr-defined]
            return
        location = " ".join(trigger.args[1:])
        async with get_session() as session:
            await set_plugin_setting(session, "weather", "location", location, channel=trigger.nick)
        await bot.reply(trigger, f"Location saved: {location}")  # type: ignore[attr-defined]
        return

    # Determine location
    if trigger.args:
        location = " ".join(trigger.args)
    else:
        async with get_session() as session:
            location = await get_plugin_setting(session, "weather", "location", channel=trigger.nick)
        if not location:
            await bot.reply(trigger, "Usage: !weather <location> (or !weather set <location> to save).")  # type: ignore[attr-defined]
            return

    result = await _get_weather(location)
    await bot.say(trigger.target, result)  # type: ignore[attr-defined]


@plugin.command(
    "forecast",
    help="Show 3-day weather forecast",
    usage="!forecast [location]",
)
async def cmd_forecast(bot: object, trigger: Trigger) -> None:
    from pybot.core.database import get_plugin_setting, get_session

    if trigger.args:
        location = " ".join(trigger.args)
    else:
        async with get_session() as session:
            location = await get_plugin_setting(session, "weather", "location", channel=trigger.nick)
        if not location:
            await bot.reply(trigger, "Usage: !forecast <location>")  # type: ignore[attr-defined]
            return

    result = await _get_forecast(location)
    for line in result:
        await bot.say(trigger.target, line)  # type: ignore[attr-defined]


async def _geocode(location: str) -> tuple[float, float, str] | None:
    """Return (lat, lon, display_name) or None."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _GEOCODE_URL,
                params={"q": location, "format": "json", "limit": 1},
                headers={"User-Agent": "PyraBot/1.0 (IRC bot; contact jarskynz@gmail.com)"},
            )
            data = resp.json()
        if not data:
            return None
        place = data[0]
        return float(place["lat"]), float(place["lon"]), place.get("display_name", location)
    except Exception:
        return None


async def _get_weather(location: str) -> str:
    geo = await _geocode(location)
    if not geo:
        return f"Could not find location: {location}"
    lat, lon, name = geo

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _WEATHER_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "wind_speed_unit": "kmh",
                    "temperature_unit": "celsius",
                },
            )
            data = resp.json()
    except Exception as exc:
        return f"Weather API error: {exc}"

    current = data.get("current", {})
    temp_c = current.get("temperature_2m", "?")
    temp_f = round(float(temp_c) * 9 / 5 + 32, 1) if isinstance(temp_c, (int, float)) else "?"
    humidity = current.get("relative_humidity_2m", "?")
    wind = current.get("wind_speed_10m", "?")
    code = current.get("weather_code", 0)
    condition = _wmo_description(code)

    city = name.split(",")[0].strip()
    return (
        f"\x02{city}\x02: {condition} | "
        f"Temp: {temp_c}°C / {temp_f}°F | "
        f"Humidity: {humidity}% | "
        f"Wind: {wind} km/h"
    )


async def _get_forecast(location: str) -> list[str]:
    geo = await _geocode(location)
    if not geo:
        return [f"Could not find location: {location}"]
    lat, lon, name = geo

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _WEATHER_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                    "forecast_days": 3,
                    "temperature_unit": "celsius",
                    "timezone": "UTC",
                },
            )
            data = resp.json()
    except Exception as exc:
        return [f"Forecast API error: {exc}"]

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    codes = daily.get("weather_code", [])

    city = name.split(",")[0].strip()
    lines = [f"\x023-day forecast for {city}\x02:"]
    for date, hi, lo, code in zip(dates, max_temps, min_temps, codes):
        lines.append(
            f"  {date}: {_wmo_description(code)} | Hi: {hi}°C  Lo: {lo}°C"
        )
    return lines


def _wmo_description(code: int) -> str:
    table = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Icy fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Heavy drizzle",
        61: "Light rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Light snow",
        73: "Snow",
        75: "Heavy snow",
        80: "Rain showers",
        81: "Moderate showers",
        82: "Violent showers",
        95: "Thunderstorm",
        96: "Thunderstorm with hail",
        99: "Thunderstorm with heavy hail",
    }
    return table.get(code, f"Unknown ({code})")
