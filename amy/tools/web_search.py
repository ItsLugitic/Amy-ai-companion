"""
tools/web_search.py — Web search via SerpAPI or DuckDuckGo fallback.
"""
import logging
import httpx
from config import settings

logger = logging.getLogger("amy.tools.web_search")


async def search(query: str) -> str:
    """
    Searches the web. Uses SerpAPI if key available, else DuckDuckGo.
    Returns a formatted string of results.
    """
    try:
        if settings.serpapi_key:
            return await _serpapi(query)
        return await _duckduckgo(query)
    except Exception as e:
        logger.error("web_search failed: %s", e)
        return "Search failed. Try again later."


async def _serpapi(query: str) -> str:
    params = {
        "q": query,
        "api_key": settings.serpapi_key,
        "num": 5,
        "hl": "en",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://serpapi.com/search", params=params)
        data = r.json()

    results = data.get("organic_results", [])[:4]
    if not results:
        return "No results found."

    lines = []
    for i, res in enumerate(results, 1):
        title   = res.get("title", "")
        snippet = res.get("snippet", "")
        link    = res.get("link", "")
        lines.append(f"{i}. {title}\n   {snippet}\n   {link}")
    return "\n\n".join(lines)


async def _duckduckgo(query: str) -> str:
    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get("https://api.duckduckgo.com/", params=params)
        data = r.json()

    abstract = data.get("AbstractText", "")
    if abstract:
        return abstract

    snippets = [
        t["Text"]
        for t in data.get("RelatedTopics", [])[:4]
        if isinstance(t, dict) and t.get("Text")
    ]
    return "\n".join(snippets) if snippets else "I couldn't find specific results for that."
