"""
core/conversation.py — Chat-scoped conversation history management.

Keyed by chat_id (not user_id): in a private chat that's the same as the
user's id, so behavior there is unchanged. In a group, it means every member
talking to/around Amy shares ONE history — which is what lets her connect
something said by one person to something a different person does later.
User turns in a group are tagged "Name: message" so she can tell people apart
inside that shared history.
"""
import logging
from config import settings
from personalities import build_initial_history

logger = logging.getLogger("amy.conversation")

# chat_id → list of {"role": ..., "content": ...}
_histories: dict[int, list[dict]] = {}


def get(chat_id: int) -> list[dict]:
    """Returns the chat's history, creating it from template if absent."""
    if chat_id not in _histories:
        _histories[chat_id] = build_initial_history()
        logger.debug("Created new history for chat %d", chat_id)
    return _histories[chat_id]


def append(chat_id: int, role: str, content: str, sender_name: str = "") -> None:
    """
    Appends a message to the chat's history.
    `sender_name` tags a user-turn with who said it (used in groups so Amy
    can tell members apart in the shared history). Ignored for non-user roles.
    """
    tag = f"{sender_name}: " if (role == "user" and sender_name) else ""
    get(chat_id).append({"role": role, "content": tag + content})


def inject_system(chat_id: int, content: str) -> None:
    """Appends a system message (e.g. for memory injection)."""
    append(chat_id, "system", content)


def trim(chat_id: int) -> None:
    """Keeps history manageable by trimming non-system messages."""
    history = _histories.get(chat_id, [])
    sys_msgs   = [m for m in history if m["role"] == "system"]
    other_msgs = [m for m in history if m["role"] != "system"]
    other_msgs = other_msgs[-settings.history_max_messages:]
    _histories[chat_id] = sys_msgs + other_msgs


def reset(chat_id: int) -> None:
    """Clears a chat's history so it gets recreated fresh on next get()."""
    _histories.pop(chat_id, None)
    logger.info("Reset history for chat %d", chat_id)


def snapshot(chat_id: int) -> list[dict]:
    """Returns a shallow copy of the history (safe to extend without mutating)."""
    return list(get(chat_id))
