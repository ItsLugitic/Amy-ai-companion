"""
tools/youtube.py — YouTube search via Invidious (free, no key needed).
Auto-translates non-English queries to English before searching.
Falls back to YouTube search URL if Invidious instances are down.
"""
import logging
import urllib.parse
import httpx

logger = logging.getLogger("amy.tools.youtube")

# Public Invidious instances — tried in order
INVIDIOUS_INSTANCES = [
    "https://invidious.snopyta.org",
    "https://vid.puffyan.us",
    "https://yt.artemislena.eu",
    "https://invidious.nerdvpn.de",
]


def _has_non_latin(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


async def search_youtube(query: str, max_results: int = 3) -> str:
    """
    Search YouTube and return top video results with links.
    Automatically translates non-English queries to English.
    """
    if not query.strip():
        return "Please specify a search query."

    # Auto-translate any non-Latin script to English for better YouTube results
    search_query = query
    if _has_non_latin(query):
        try:
            from tools.translate import to_english
            translated = await to_english(query)
            if translated and translated.strip() and translated.lower() != query.lower():
                logger.info("YouTube query translated: '%s' → '%s'", query, translated)
                search_query = translated
        except Exception as e:
            logger.warning("YouTube query translation failed: %s", e)

    encoded  = urllib.parse.quote(search_query)
    yt_url   = f"https://www.youtube.com/results?search_query={encoded}"
    fallback = f"YouTube: {yt_url}"

    # Try Invidious instances for structured results
    for instance in INVIDIOUS_INSTANCES:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"{instance}/api/v1/search",
                    params={
                        "q":      search_query,
                        "type":   "video",
                        "fields": "title,videoId,author,lengthSeconds",
                    },
                )
            if r.status_code != 200:
                continue

            results = r.json()
            if not results or not isinstance(results, list):
                continue

            lines = []
            for v in results[:max_results]:
                title    = v.get("title", "")
                vid_id   = v.get("videoId", "")
                author   = v.get("author", "")
                secs     = v.get("lengthSeconds", 0)
                duration = f"{secs // 60}:{secs % 60:02d}" if secs else ""
                url      = f"https://www.youtube.com/watch?v={vid_id}"
                lines.append(f"• {title}\n  {author} | {duration}\n  {url}")

            if lines:
                header = f"YouTube results for '{query}':" if query != search_query \
                         else f"YouTube results:"
                return header + "\n\n" + "\n\n".join(lines)

        except Exception as e:
            logger.warning("Invidious %s failed: %s", instance, e)
            continue

    # All Invidious instances failed — return search URL
    logger.info("All Invidious instances failed, returning search URL")
    return fallback


async def search_instagram(query: str) -> str:
    """Instagram search URL (direct API requires auth)."""
    encoded = urllib.parse.quote(query)
    return (
        f"Instagram search for '{query}':\n"
        f"https://www.instagram.com/explore/tags/{encoded}/\n"
        "(Direct content requires Instagram login)"
    )
