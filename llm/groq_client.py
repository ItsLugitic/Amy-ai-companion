"""
llm/groq_client.py — Groq LLM client with multi-key rotation.

Groq (console.groq.com):
  - Free API key, no credit card required
  - Key format: gsk_...

Set GROQ_API_KEYS to a comma-separated list of keys (e.g. from a few free
accounts) — when one hits its rate limit, calls rotate to the next key for
the SAME model. Models are never swapped on rate limit anymore: that used to
mean falling back to a weaker/different model mid-conversation, which could
shift personality and output quality. Now only the key changes; the model
(and therefore personality/quality) stays identical. Rate limits on Groq are
per (key, model) pair, so a key exhausted on the chat model can still have
budget left on the fast/vision models.

Three models are used on purpose:
  - groq_chat_model   (default: openai/gpt-oss-120b) — real personality replies.
  - groq_fast_model   (default: openai/gpt-oss-20b)  — cheap "should I jump in?"
    decisions for passive group listening — its own separate quota per key.
  - groq_vision_model (default: qwen/qwen3.6-27b) — the only Groq model with
    vision support at the moment.

Model IDs are configurable via env vars (GROQ_CHAT_MODEL / GROQ_FAST_MODEL /
GROQ_VISION_MODEL) since Groq retires model IDs on fairly short notice —
check https://console.groq.com/docs/deprecations if requests start failing.
"""
import logging
import random
import time
from openai import OpenAI, RateLimitError, APIStatusError, APITimeoutError
from config import settings

logger = logging.getLogger("amy.llm")

_clients: list[OpenAI] = []
# (key_index, model) → monotonic time until which this key is skipped for that model
_exhausted: dict[tuple[int, str], float] = {}
# model → key index to try first (sticky, so a working key keeps getting used)
_current_idx: dict[str, int] = {}


def _build_clients() -> list[OpenAI]:
    global _clients
    if not _clients:
        if not settings.groq_api_keys:
            raise RuntimeError("No Groq API key configured (GROQ_API_KEY / GROQ_API_KEYS)")
        _clients = [
            OpenAI(base_url="https://api.groq.com/openai/v1", api_key=k)
            for k in settings.groq_api_keys
        ]
        logger.info("Groq client pool: %d key(s)", len(_clients))
    return _clients


def _cooldown_seconds_for(e: Exception) -> int:
    """Daily-cap errors get a long cooldown; per-minute caps get a short one."""
    msg = str(e).lower()
    if any(w in msg for w in ("per day", "daily", "rpd", "tpd")):
        return 6 * 3600
    return 60


def _complete(model: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    """Tries every key (starting from the sticky current one) for this exact model, never swapping models."""
    clients = _build_clients()
    n = len(clients)
    now = time.monotonic()
    start = _current_idx.get(model, 0) % n

    order = [(start + i) % n for i in range(n)]
    candidates = [i for i in order if _exhausted.get((i, model), 0.0) <= now] or order

    last_err: Exception | None = None
    for idx in candidates:
        try:
            completion = clients[idx].chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=12,
            )
            _current_idx[model] = idx   # stick with the key that just worked
            return completion.choices[0].message.content.strip()

        except RateLimitError as e:
            cd = _cooldown_seconds_for(e)
            _exhausted[(idx, model)] = now + cd
            logger.warning("Key #%d rate-limited on %s — cooling down %ds", idx, model, cd)
            last_err = e
            continue

        except APITimeoutError as e:
            logger.warning("Key #%d timed out on %s", idx, model)
            last_err = e
            continue

        except APIStatusError as e:
            if e.status_code in (429, 502, 503, 529):
                _exhausted[(idx, model)] = now + 60
                logger.warning("Key #%d HTTP %d on %s", idx, e.status_code, model)
                last_err = e
                continue
            raise

    raise last_err or RuntimeError(f"All Groq keys failed for {model}")


def chat(
    messages: list[dict],
    max_tokens: int = None,
    temperature: float = None,
) -> str:
    """Main personality replies — same model always, only the key rotates."""
    max_tokens  = max_tokens  or settings.llm_max_tokens
    temperature = temperature or settings.llm_temperature
    try:
        return _complete(settings.groq_chat_model, messages, max_tokens, temperature)
    except Exception as e:
        logger.error("All keys exhausted/failed for chat model: %s", e)
        return _exhausted_fallback(messages)


def fast_chat(messages: list[dict], max_tokens: int = 60, temperature: float = 0.3) -> str:
    """Cheap/quick call on the small model — used for the passive-engagement yes/no gate."""
    try:
        return _complete(settings.groq_fast_model, messages, max_tokens, temperature)
    except Exception as e:
        logger.warning("fast_chat exhausted (%s) — defaulting to 'no'", e)
        return "no"


def vision_describe(image_b64: str, question: str = "") -> str:
    prompt = question if question else (
        "Describe the main contents of this image in 2-3 sentences. "
        "Be specific — mention objects, people, places, text, colors, style."
    )
    clients = _build_clients()
    n = len(clients)
    now = time.monotonic()
    model = settings.groq_vision_model
    start = _current_idx.get(model, 0) % n
    order = [(start + i) % n for i in range(n)]
    candidates = [i for i in order if _exhausted.get((i, model), 0.0) <= now] or order

    for idx in candidates:
        try:
            completion = clients[idx].chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }],
                max_tokens=400,
                timeout=15,
            )
            _current_idx[model] = idx
            return completion.choices[0].message.content.strip()
        except RateLimitError as e:
            cd = _cooldown_seconds_for(e)
            _exhausted[(idx, model)] = now + cd
            logger.warning("Key #%d rate-limited on vision — cooling down %ds", idx, cd)
            continue
        except Exception as e:
            logger.error("vision_describe error on key #%d: %s", idx, e)
            continue
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


def _exhausted_fallback(messages: list[dict]) -> str:
    """
    Picked when EVERY key is exhausted/failing for the chat model.
    Stays in character instead of sounding like a system error message.
    """
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    fa = sum(1 for c in last_user if "\u0600" <= c <= "\u06FF") > len(last_user) * 0.15

    if fa:
        lines = [
            f"emotion: annoyed\nوایسا... فکر کنم {settings.creator_name} باز لپ‌تاپشو خاموش کرده. یه لحظه صبر کن روشنش کنه.",
            f"emotion: bored\nمخم امروز کار نمی‌کنه... برو از {settings.creator_name} بپرس چرا API رو شارژ نکرده.",
            "emotion: tsundere\nیه لحظه دارم فکر می‌کنم ها... باشه دروغ گفتم، فقط گیر کردم. دوباره بپرس.",
        ]
    else:
        lines = [
            f"emotion: annoyed\nhold on... pretty sure {settings.creator_name}'s laptop died again. give it a sec.",
            f"emotion: bored\nmy brain's not loading right now. go yell at {settings.creator_name}, not me.",
            "emotion: tsundere\ni'm 'thinking' very hard right now. ask me again in a bit, baka.",
        ]
    return random.choice(lines)
