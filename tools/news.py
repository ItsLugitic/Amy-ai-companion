"""
tools/news.py — Latest news via GNews free API or RSS fallback.
GNEWS_API_KEY is optional — if absent, uses BBC/Reuters RSS (always free).
"""
import logging
import httpx
import os
from config import settings

logger = logging.getLogger("amy.tools.news")


async def get_news(query: str = "", category: str = "general", lang: str = "en") -> str:
    """
    Returns 3-5 latest news headlines.
    query: specific search term (optional)
    category: general | technology | sports | business | science | health
    """
    gnews_key = getattr(settings, "gnews_api_key", None)

    if gnews_key:
        return await _gnews(query, category, lang, gnews_key)
    return await _rss_fallback(query, lang)


async def _gnews(query: str, category: str, lang: str, api_key: str) -> str:
    base = "https://gnews.io/api/v4"
    endpoint = f"{base}/search" if query else f"{base}/top-headlines"
    params = {
        "apikey":   api_key,
        "lang":     lang,
        "max":      5,
        "category": category,
    }
    if query:
        params["q"] = query

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r    = await client.get(endpoint, params=params)
            data = r.json()

        articles = data.get("articles", [])
        if not articles:
            return "No news found."

        lines = []
        for a in articles[:5]:
            title  = a.get("title", "")
            source = a.get("source", {}).get("name", "")
            url    = a.get("url", "")
            lines.append(f"• {title} [{source}]\n  {url}")
        return "\n\n".join(lines)
    except Exception as e:
        logger.error("GNews error: %s", e)
        return await _rss_fallback(query, lang)


async def _rss_fallback(query: str, lang: str) -> str:
    """Use BBC/Reuters RSS — no key needed."""
    if lang == "fa":
        rss_url = "https://feeds.bbcpersian.com/persian/iran"
    else:
        rss_url = "https://feeds.bbci.co.uk/news/rss.xml" if not query \
                  else f"https://news.google.com/rss/search?q={query}&hl=en"

    try:
        async with httpx.AsyncClient(
            timeout=10,
            headers={"User-Agent": "Amy-Bot/4.0"},
            follow_redirects=True,
        ) as client:
            r = await client.get(rss_url)

        # Minimal RSS parse without lxml
        import re
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
        if not titles:
            titles = re.findall(r'<title>(.*?)</title>', r.text)

        # Skip first entry (usually feed title)
        items = [t for t in titles[1:6] if t and len(t) > 10]
        if not items:
            return "No news available right now."

        return "Latest news:\n" + "\n".join(f"• {t}" for t in items)
    except Exception as e:
        logger.error("RSS fallback error: %s", e)
        return "Couldn't fetch news right now."
