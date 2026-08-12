"""
tools/image_generate.py — Generate images via Pollinations.ai (free, no key).
Includes debug logging and prompt validation.
"""
import logging
import urllib.parse
from config import settings

logger = logging.getLogger("amy.tools.image_generate")

# Pollinations works best with short, descriptive English prompts
_MAX_PROMPT_CHARS = 400


def _sanitize_prompt(prompt: str) -> str:
    """Clean and truncate the prompt for URL safety."""
    cleaned = prompt.strip()
    # Remove any Persian/Arabic chars that Pollinations can't process well
    if len(cleaned) > _MAX_PROMPT_CHARS:
        cleaned = cleaned[:_MAX_PROMPT_CHARS]
        logger.warning("Prompt truncated to %d chars", _MAX_PROMPT_CHARS)
    return cleaned


async def generate(prompt: str) -> str | None:
    """
    Returns a Pollinations.ai URL for the given prompt.
    This is async to match the tool interface, but no HTTP call is needed —
    the URL itself IS the generator (Pollinations renders on first access).
    """
    if not prompt or not prompt.strip():
        logger.error("generate_image called with empty prompt")
        return None

    clean = _sanitize_prompt(prompt)
    logger.info("generate_image prompt: '%s'", clean)

    try:
        encoded = urllib.parse.quote(clean, safe="")
        # Add seed for more consistent results + nologo flag
        url = f"{settings.pollinations_base_url}/{encoded}?nologo=true&seed=42"
        logger.info("Generated Pollinations URL: %s", url[:120])
        return url
    except Exception as e:
        logger.error("image_generate error building URL: %s", e)
        return None
