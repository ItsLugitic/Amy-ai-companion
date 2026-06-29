"""
llm/nvidia_client.py — OpenRouter LLM client with automatic model fallback.

OpenRouter:
  - No phone number, no credit card — email only
  - 20+ free models, one key, OpenAI-compatible endpoint
  - Sign up: https://openrouter.ai  →  Keys  →  Create Key
  - Free models are identified by the ":free" suffix

Rate limits on free tier (as of 2026):
  - 20 RPM, 50 req/day without credits
  - 20 RPM, 1000 req/day with $10+ credits loaded (optional)
"""
import logging
import time
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError
from config import settings

logger = logging.getLogger("amy.llm")

_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=settings.nvidia_api_key,          # reuses same env var — just set NVIDIA_API_KEY=sk-or-...
    default_headers={
        "HTTP-Referer": "https://github.com/amy-bot",   # required by OpenRouter (any URL is fine)
        "X-Title": "Amy Bot",
    },
)

# ── Free model priority list ───────────────────────────────────────────────────
# All end with :free — tried in order, moves to next on rate limit / error
# Ordered: best chat quality first, lightweight fallbacks last
NVIDIA_MODELS = [
    "google/gemini-2.0-flash-exp:free",          # 1st: Gemini 2.0, fast + smart
    "meta-llama/llama-3.3-70b-instruct:free",    # 2nd: Llama 3.3 70B, great quality
    "mistralai/mistral-small-3.2-24b-instruct:free",  # 3rd: Mistral 24B, strong
    "deepseek/deepseek-r1-0528:free",            # 4th: DeepSeek R1, reasoning
    "qwen/qwen3-30b-a3b:free",                   # 5th: Qwen3, multilingual/Persian
    "microsoft/phi-4-reasoning:free",            # 6th: Phi-4, reasoning
    "meta-llama/llama-3.1-8b-instruct:free",     # 7th: lightweight last resort
]

# Vision model — for image analysis
VISION_MODEL = "google/gemini-2.0-flash-exp:free"   # Gemini supports vision on free tier


def chat(
    messages: list[dict],
    max_tokens: int = None,
    temperature: float = None,
) -> str:
    """
    Calls OpenRouter with automatic fallback across all free models.
    Returns the assistant's raw text response.
    """
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
            logger.warning("Rate limit on %s — trying next model", model)
            time.sleep(0.5)
            continue

        except APITimeoutError:
            logger.warning("Timeout on %s — trying next model", model)
            continue

        except APIStatusError as e:
            if e.status_code in (429, 503, 502, 529):
                logger.warning("HTTP %d on %s — trying next model", e.status_code, model)
                time.sleep(0.3)
                continue
            logger.error("API error on %s: %s", model, e)
            break

        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("rate_limit", "429", "quota", "too many", "timeout", "overload")):
                logger.warning("Soft rate limit on %s — trying next model", model)
                continue
            logger.error("Unexpected error on %s: %s", model, e)
            break

    return "emotion: worried\nSomething went wrong... try again in a moment."


def vision_describe(image_b64: str, question: str = "") -> str:
    """
    Uses vision model to analyze an image.
    If a specific question is provided, answers that question directly.
    Otherwise gives a description.
    image_b64: base64-encoded JPEG/PNG string.
    """
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
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
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
    """
    Ask the vision model a specific question about an image.
    Returns the model's direct answer (not wrapped in Amy's personality).

    lang: "fa" or "en" — used to ask the question in the right language.
    """
    if question:
        if lang == "fa":
            prompt = f"به این سوال درباره تصویر به فارسی پاسخ بده: {question}"
        else:
            prompt = (
                f"Answer this question about the image directly and specifically: {question}\n"
                "Be concrete — if it's a car, name the make/model/year. "
                "If it's code, identify the exact bug. "
                "If it's a place, name it. If it's food, name the dish."
            )
    else:
        prompt = (
            "Describe what you see in this image in 2-3 sentences. "
            "Be specific: objects, people, places, text, colors, style, mood."
        )

    result = vision_describe(image_b64, prompt)
    if not result:
        return "نتونستم تصویر رو آنالیز کنم." if lang == "fa" else "Couldn't analyze the image."
    return result
