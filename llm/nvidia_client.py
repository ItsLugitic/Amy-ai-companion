"""
llm/nvidia_client.py — OpenRouter LLM client with automatic model fallback.

OpenRouter:
  - No phone number, no credit card — email only
  - Sign up: https://openrouter.ai → Keys → Create Key
  - Key format: sk-or-...

Strategy:
  1. Try specific free models in priority order
  2. If ALL fail → fall back to openrouter/free (auto-selects any available free model)
  This means Amy ALWAYS has a working model, even when specific ones go down.
"""
import logging
import time
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError
from config import settings

logger = logging.getLogger("amy.llm")

_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.nvidia_api_key,
    default_headers={
        "HTTP-Referer": "https://github.com/amy-bot",
        "X-Title": "Amy Bot",
    },
)

# ── Model priority list (verified working June 2026) ──────────────────────────
# If one hits rate limit or 404, automatically moves to next.
# Last entry is openrouter/free — always works as final fallback.
NVIDIA_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",        # 1st: best quality free
    "mistralai/mistral-small-3.2-24b-instruct:free",  # 2nd: strong, reliable
    "deepseek/deepseek-r1-0528:free",                 # 3rd: great reasoning
    "qwen/qwen3-30b-a3b:free",                        # 4th: good Persian support
    "meta-llama/llama-3.1-8b-instruct:free",          # 5th: fast lightweight
    "openrouter/free",                                # 6th: auto-picks any free model
]

# Vision: use the free router with vision support filter
VISION_MODEL = "openrouter/free"   # auto-selects a vision-capable free model


def chat(
    messages: list[dict],
    max_tokens: int = None,
    temperature: float = None,
) -> str:
    max_tokens  = max_tokens  or settings.llm_max_tokens
    temperature = temperature or settings.llm_temperature

    for model in NVIDIA_MODELS:
        try:
            completion = _client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=30,
            )
            logger.info("OpenRouter model used: %s", model)
            return completion.choices[0].message.content.strip()

        except RateLimitError:
            logger.warning("Rate limit on %s — trying next", model)
            time.sleep(0.5)
            continue

        except APITimeoutError:
            logger.warning("Timeout on %s — trying next", model)
            continue

        except APIStatusError as e:
            if e.status_code in (404, 429, 502, 503, 529):
                logger.warning("HTTP %d on %s — trying next", e.status_code, model)
                time.sleep(0.3)
                continue
            logger.error("API error on %s: %s", model, e)
            break

        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("rate_limit", "429", "quota", "timeout", "overload", "no endpoints")):
                logger.warning("Soft error on %s (%s) — trying next", model, err[:60])
                continue
            logger.error("Unexpected error on %s: %s", model, e)
            break

    return "emotion: worried\nSomething went wrong... try again in a moment."


def vision_describe(image_b64: str, question: str = "") -> str:
    prompt = question if question else (
        "Describe the main contents of this image in 2-3 sentences. "
        "Be specific — mention objects, people, places, text, colors, style."
    )
    try:
        completion = _client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=400,
            timeout=25,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error("vision_describe error: %s", e)
        return ""


def vision_ask(image_b64: str, question: str, lang: str = "en") -> str:
    if question:
        if lang == "fa":
            prompt = f"به این سوال درباره تصویر به فارسی پاسخ بده: {question}"
        else:
            prompt = (
                f"Answer this question about the image directly and specifically: {question}\n"
                "Be concrete — if it's a car, name make/model/year. "
                "If it's code, identify the exact bug. "
                "If it's a place, name it. If it's food, name the dish."
            )
    else:
        prompt = (
            "Describe what you see in 2-3 sentences. "
            "Be specific: objects, people, places, text, colors, style, mood."
        )

    result = vision_describe(image_b64, prompt)
    if not result:
        return "نتونستم تصویر رو آنالیز کنم." if lang == "fa" else "Couldn't analyze the image."
    return result
