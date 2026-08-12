"""
core/moderation.py — Strike tracking for auto-moderation.

In-memory, same pattern as core/conversation.py and core/emotion_engine.py —
resets on restart, which is fine here since strikes also decay on their own.
"""
import logging
import time

logger = logging.getLogger("amy.moderation")

# (chat_id, user_id) → (strike_count, last_strike_monotonic_time)
_strikes: dict[tuple[int, int], tuple[int, float]] = {}


def add_strike(chat_id: int, user_id: int, reset_seconds: int) -> int:
    """Records a strike, resetting the counter first if the last one is stale. Returns the new count."""
    key = (chat_id, user_id)
    now = time.monotonic()
    count, last = _strikes.get(key, (0, 0.0))
    if last and (now - last) > reset_seconds:
        count = 0
    count += 1
    _strikes[key] = (count, now)
    logger.info("Strike #%d for user %d in chat %d", count, user_id, chat_id)
    return count


def get_strikes(chat_id: int, user_id: int) -> int:
    return _strikes.get((chat_id, user_id), (0, 0.0))[0]


def clear_strikes(chat_id: int, user_id: int) -> None:
    _strikes.pop((chat_id, user_id), None)
