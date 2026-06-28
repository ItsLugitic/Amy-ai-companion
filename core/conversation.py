"""
core/conversation.py — Per-user conversation history management.
"""
import logging
from config import settings
from personalities import build_initial_history

logger = logging.getLogger("amy.conversation")

# uid → list of {"role": ..., "content": ...}
_histories: dict[int, list[dict]] = {}


def get(user_id: int) -> list[dict]:
    """Returns the user's history, creating it from template if absent."""
    if user_id not in _histories:
        _histories[user_id] = build_initial_history()
        logger.debug("Created new history for user %d", user_id)
    return _histories[user_id]


def append(user_id: int, role: str, content: str) -> None:
    """Appends a message to the user's history."""
    get(user_id).append({"role": role, "content": content})


def inject_system(user_id: int, content: str) -> None:
    """Appends a system message (e.g. for memory injection)."""
    append(user_id, "system", content)


def trim(user_id: int) -> None:
    """Keeps history manageable by trimming non-system messages."""
    history = _histories.get(user_id, [])
    sys_msgs   = [m for m in history if m["role"] == "system"]
    other_msgs = [m for m in history if m["role"] != "system"]
    other_msgs = other_msgs[-settings.history_max_messages:]
    _histories[user_id] = sys_msgs + other_msgs


def reset(user_id: int) -> None:
    """Clears a user's history so it gets recreated fresh on next get()."""
    _histories.pop(user_id, None)
    logger.info("Reset history for user %d", user_id)


def snapshot(user_id: int) -> list[dict]:
    """Returns a shallow copy of the history (safe to extend without mutating)."""
    return list(get(user_id))
