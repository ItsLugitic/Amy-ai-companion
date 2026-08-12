"""
tools/translate.py — Translation using MyMemory free API (no key needed).
Also used internally to translate Pixabay/YouTube queries to English.
"""
import logging
import urllib.parse
import httpx

logger = logging.getLogger("amy.tools.translate")

MYMEMORY_URL = "https://api.mymemory.translated.net/get"


async def translate(text: str, target_lang: str = "en", source_lang: str = "auto") -> str:
    """
    Translate text to target_lang using MyMemory free API.
    Returns translated string or original on failure.
    """
    if not text or not text.strip():
        return text

    lang_pair = f"{source_lang}|{target_lang}" if source_lang != "auto" else f"auto|{target_lang}"
    params = {"q": text, "langpair": lang_pair}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(MYMEMORY_URL, params=params)
            data = r.json()

        translated = data.get("responseData", {}).get("translatedText", "")
        if translated and translated.lower() != text.lower():
            logger.info("Translated '%s' → '%s'", text[:40], translated[:40])
            return translated
        return text
    except Exception as e:
        logger.error("translate error: %s", e)
        return text


async def to_english(text: str) -> str:
    """Shortcut: translate any language → English (for Pixabay/YouTube queries)."""
    return await translate(text, target_lang="en", source_lang="auto")


async def from_english(text: str, target_lang: str) -> str:
    """Translate English text to target language."""
    return await translate(text, target_lang=target_lang, source_lang="en")
