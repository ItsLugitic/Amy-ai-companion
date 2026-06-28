"""
tools/image_search.py — Fetch real photos via Pixabay.
Automatically translates non-English queries to English before searching.
"""
import logging
import aiohttp
from config import settings

logger = logging.getLogger("amy.tools.image_search")

_FA_FILLER = [
    "عکس", "تصویر", "عکسی از", "تصویری از", "یه عکس", "یک عکس",
    "بده", "بفرست", "نشونم بده", "برام بفرست",
]
_EN_FILLER = [
    "photo of", "picture of", "image of", "show me a",
    "send me a", "send me", "find me a", "give me a",
]


def _strip_filler(query: str) -> str:
    cleaned = query.strip()
    for w in _FA_FILLER + _EN_FILLER:
        cleaned = cleaned.replace(w, "").strip()
    return cleaned or query


def _has_non_latin(text: str) -> bool:
    """True if text contains Persian/Arabic or other non-ASCII scripts."""
    return any(ord(c) > 127 for c in text)


async def get_url(query: str) -> str | None:
    """
    Search Pixabay. Automatically translates non-English queries.
    Returns image URL or None.
    """
    if not settings.pixabay_api_key:
        logger.error("PIXABAY_API_KEY not set")
        return None

    # Strip filler words first
    clean = _strip_filler(query)

    # Auto-translate if query contains non-Latin chars
    if _has_non_latin(clean):
        from tools.translate import to_english
        translated = await to_english(clean)
        logger.info("Auto-translated query: '%s' → '%s'", clean, translated)
        clean = translated

    logger.info("image_search: '%s'", clean)

    params = {
        "key":        settings.pixabay_api_key,
        "q":          clean,
        "image_type": "photo",
        "safesearch": "true",
        "per_page":   10,
        "lang":       "en",
    }

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12)
        ) as session:
            async with session.get("https://pixabay.com/api/", params=params) as resp:
                logger.debug("Pixabay HTTP %d for '%s'", resp.status, clean)

                if resp.status == 400:
                    logger.error("Pixabay 400 — invalid query: '%s'", clean)
                    return None
                if resp.status == 429:
                    logger.error("Pixabay rate limit")
                    return None
                if resp.status != 200:
                    logger.error("Pixabay HTTP %d", resp.status)
                    return None

                data  = await resp.json()
                hits  = data.get("hits", [])
                total = data.get("totalHits", 0)
                logger.info("Pixabay: %d results for '%s'", total, clean)

                if not hits:
                    return None

                url = hits[0]["largeImageURL"]
                logger.info("Image URL: %s", url[:80])
                return url

    except aiohttp.ClientConnectorError as e:
        logger.error("Pixabay connection error: %s", e)
        return None
    except aiohttp.ClientTimeout:
        logger.error("Pixabay timeout")
        return None
    except Exception as e:
        logger.error("image_search unexpected error: %s", e)
        return None
