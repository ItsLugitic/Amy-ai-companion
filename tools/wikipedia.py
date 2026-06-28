"""
tools/wikipedia.py — Wikipedia summary via free REST API (no key needed).
"""
import logging
import httpx
import urllib.parse

logger = logging.getLogger("amy.tools.wikipedia")

WIKI_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_FA_URL = "https://fa.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"


async def search_and_summarize(query: str, lang: str = "en") -> str:
    """
    Search Wikipedia for query, return a short summary.
    lang: 'fa' for Persian Wikipedia, 'en' for English.
    """
    if not query.strip():
        return "No query provided."

    try:
        # Step 1: Search for the best matching page title
        async with httpx.AsyncClient(timeout=8) as client:
            search_r = await client.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list":   "search",
                    "srsearch": query,
                    "srlimit": 1,
                    "format": "json",
                },
            )
            search_data = search_r.json()

        hits = search_data.get("query", {}).get("search", [])
        if not hits:
            # Try English as fallback for Persian queries
            if lang == "fa":
                return await search_and_summarize(query, lang="en")
            return f"No Wikipedia article found for '{query}'."

        title   = hits[0]["title"]
        enc     = urllib.parse.quote(title.replace(" ", "_"))
        api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{enc}"

        # Step 2: Get page summary
        async with httpx.AsyncClient(timeout=8) as client:
            r    = await client.get(api_url)
            data = r.json()

        extract = data.get("extract", "")
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

        if not extract:
            return f"No summary available for '{title}'."

        # Truncate to ~500 chars
        if len(extract) > 500:
            extract = extract[:500] + "..."

        return f"Wikipedia — {title}:\n{extract}\n{page_url}"

    except Exception as e:
        logger.error("wikipedia error for '%s': %s", query, e)
        return f"Couldn't fetch Wikipedia info for '{query}'."
