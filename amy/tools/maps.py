"""
tools/maps.py — Generate Google Maps links and get location info.
No API key needed for link generation.
For geocoding uses Nominatim (OpenStreetMap, free, no key).
"""
import logging
import urllib.parse
import httpx

logger = logging.getLogger("amy.tools.maps")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def get_map_link(location: str) -> str:
    """
    Returns a Google Maps link + brief address info for the location.
    """
    if not location.strip():
        return "Please specify a location."

    encoded = urllib.parse.quote(location)
    maps_url = f"https://www.google.com/maps/search/?api=1&query={encoded}"

    try:
        # Get address details from Nominatim
        async with httpx.AsyncClient(
            timeout=8,
            headers={"User-Agent": "AmyBot/4.0 (telegram bot)"},
        ) as client:
            r    = await client.get(
                NOMINATIM_URL,
                params={"q": location, "format": "json", "limit": 1},
            )
            data = r.json()

        if data:
            display_name = data[0].get("display_name", location)
            lat  = float(data[0]["lat"])
            lon  = float(data[0]["lon"])
            precise_url = f"https://www.google.com/maps?q={lat},{lon}"
            return (
                f"Location: {display_name}\n"
                f"Coordinates: {lat:.4f}, {lon:.4f}\n"
                f"{precise_url}"
            )

        return f"Maps: {maps_url}"

    except Exception as e:
        logger.error("maps error for '%s': %s", location, e)
        return f"Maps link: {maps_url}"
