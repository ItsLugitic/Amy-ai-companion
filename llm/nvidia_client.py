"""
llm/nvidia_client.py — NVIDIA NIM API client.

vision_ask(): sends image + specific question directly to vision model.
              The vision model answers the question itself (not just describes).
chat():       text chat with fallback across free models.
"""
import logging
import time
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError
from config import settings

logger = logging.getLogger("amy.llm")

_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=settings.nvidia_api_key,
)

NVIDIA_MODELS = [
    "meta/llama-3.3-70b-instruct",
    "mistralai/mistral-large-2-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1",
    "google/gemma-3-27b-it",
    "moonshotai/kimi-k2-instruct",
    "meta/llama-3.1-8b-instruct",
]

VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"


def chat(
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float | None = None,
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
                timeout=25,
            )
            logger.info("NIM chat model: %s", model)
            return completion.choices[0].message.content.strip()
        except RateLimitError:
            logger.warning("Rate limit: %s", model)
            time.sleep(0.3)
        except APITimeoutError:
            logger.warning("Timeout: %s", model)
        except APIStatusError as e:
            if e.status_code in (429, 503, 502):
                logger.warning("HTTP %d: %s", e.status_code, model)
            else:
                logger.error("API error on %s: %s", model, e)
                break
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("rate_limit", "429", "quota", "too many", "timeout")):
                logger.warning("Soft limit: %s", model)
            else:
                logger.error("Unexpected on %s: %s", model, e)
                break

    return "emotion: worried\nSomething went wrong... try again in a moment."


def vision_ask(image_b64: str, question: str, language_hint: str = "en") -> str:
    """
    Sends the image + the user's ACTUAL question to the vision model.
    The vision model answers directly — not just describes.

    language_hint: 'fa' for Persian, 'en' for English.
    """
    lang_instruction = (
        "پاسخ را به فارسی بده." if language_hint == "fa"
        else "Answer in English."
    )

    system_msg = (
        "You are Amy, a helpful and sharp AI assistant. "
        "You are looking at an image the user sent. "
        "Answer the user's question about the image directly and accurately. "
        "If you see a car, identify the make/model/year if possible. "
        "If you see code, explain what's wrong or what it does. "
        "If you see food, identify it. "
        "Be specific and factual. Stay in character as Amy (tsundere, concise). "
        f"{lang_instruction}"
    )

    user_content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
        },
        {
            "type": "text",
            "text": question if question else (
                "این عکس چیه؟ توضیح بده." if language_hint == "fa"
                else "What is in this image? Describe and identify everything you see."
            ),
        },
    ]

    try:
        completion = _client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=400,
            timeout=25,
        )
        result = completion.choices[0].message.content.strip()
        logger.info("Vision answer (%d chars): %s...", len(result), result[:60])
        return result
    except Exception as e:
        logger.error("vision_ask error: %s", e)
        return ""


# Keep old name as alias for any remaining callers
def vision_describe(image_b64: str) -> str:
    return vision_ask(image_b64, "Describe what you see in this image in detail.")
