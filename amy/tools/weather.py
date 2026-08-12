"""
tools/weather.py — Weather via Open-Meteo (completely free, no key needed).
Uses wttr.in for city name → coordinates, Open-Meteo for actual weather.
"""
import logging
import httpx

logger = logging.getLogger("amy.tools.weather")

WTTR_URL      = "https://wttr.in/{city}?format=j1"
GEOCODE_URL   = "https://geocoding-api.open-meteo.com/v1/search"
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Light snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Light showers", 81: "Moderate showers", 82: "Heavy showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail",
}


async def get_weather(city: str) -> str:
    """Returns a formatted weather string for the given city."""
    city = city.strip()
    if not city:
        return "Please specify a city."

    try:
        # Step 1: Geocode city name
        async with httpx.AsyncClient(timeout=8) as client:
            geo_r = await client.get(
                GEOCODE_URL,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            geo = geo_r.json()

        results = geo.get("results", [])
        if not results:
            return f"City '{city}' not found."

        loc     = results[0]
        lat     = loc["latitude"]
        lon     = loc["longitude"]
        name    = loc.get("name", city)
        country = loc.get("country", "")

        # Step 2: Get weather
        async with httpx.AsyncClient(timeout=8) as client:
            wx_r = await client.get(
                OPENMETEO_URL,
                params={
                    "latitude":              lat,
                    "longitude":             lon,
                    "current":               "temperature_2m,apparent_temperature,weathercode,windspeed_10m,relativehumidity_2m",
                    "wind_speed_unit":       "kmh",
                    "temperature_unit":      "celsius",
                    "timezone":              "auto",
                },
            )
            wx = wx_r.json()

        cur   = wx.get("current", {})
        temp  = cur.get("temperature_2m", "?")
        feel  = cur.get("apparent_temperature", "?")
        wind  = cur.get("windspeed_10m", "?")
        hum   = cur.get("relativehumidity_2m", "?")
        code  = cur.get("weathercode", 0)
        cond  = WMO_CODES.get(code, "Unknown")

        return (
            f"Weather in {name}, {country}:\n"
            f"{cond} | {temp}°C (feels like {feel}°C)\n"
            f"Wind: {wind} km/h | Humidity: {hum}%"
        )

    except Exception as e:
        logger.error("weather error for '%s': %s", city, e)
        return f"Couldn't get weather for '{city}'."
