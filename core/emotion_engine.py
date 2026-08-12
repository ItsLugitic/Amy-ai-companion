"""
core/emotion_engine.py — Tracks Amy's emotional state per user.
Emotion can shift based on user interactions over time.
"""
import logging
from models import Emotion

logger = logging.getLogger("amy.emotion")

# uid → current dominant emotion
_states: dict[int, Emotion] = {}

# Simple sentiment keywords → emotion nudge
_POSITIVE_WORDS = {"love", "thanks", "thank", "great", "awesome", "good", "nice", "cute", "دوست", "ممنون", "عالی"}
_NEGATIVE_WORDS = {"hate", "stupid", "dumb", "ugly", "bad", "بد", "احمق", "زشت"}
_EMOTIONAL_WORDS = {"scared", "sad", "cry", "lonely", "hurt", "upset", "ترسیدم", "غمگین", "تنها"}


def get_state(chat_id: int) -> Emotion:
    return _states.get(chat_id, Emotion.TSUNDERE)


def update_state(chat_id: int, new_emotion: Emotion, user_text: str = "") -> Emotion:
    """
    Updates Amy's emotional state based on the LLM's chosen emotion
    and optionally nudges it based on user sentiment.
    Returns the final emotion to use.
    """
    text_lower = user_text.lower()

    # Nudge based on user's message sentiment
    if any(w in text_lower for w in _EMOTIONAL_WORDS):
        # Amy gets worried/caring when user seems distressed
        new_emotion = Emotion.WORRIED

    _states[chat_id] = new_emotion
    logger.debug("Emotion for chat %d: %s", chat_id, new_emotion)
    return new_emotion


def reset_state(chat_id: int) -> None:
    _states.pop(chat_id, None)
