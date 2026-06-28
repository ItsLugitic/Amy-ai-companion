"""
models/schemas.py — Shared data models used across the app.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Emotion(str, Enum):
    NEUTRAL      = "neutral"
    HAPPY        = "happy"
    SAD          = "sad"
    ANGRY        = "angry"
    ANNOYED      = "annoyed"
    SHY          = "shy"
    TSUNDERE     = "tsundere"
    WORRIED      = "worried"
    CUTE_PLAYFUL = "cute_playful"
    TEASING      = "teasing"
    FLIRTY       = "flirty"
    WHISPER      = "whisper"
    BORED        = "bored"
    EXCITED      = "excited"

    @classmethod
    def from_str(cls, value: str) -> "Emotion":
        try:
            return cls(value.lower().strip())
        except ValueError:
            return cls.NEUTRAL


EMOTION_EMOJI: dict[Emotion, str] = {
    Emotion.NEUTRAL:      "",
    Emotion.HAPPY:        "😊",
    Emotion.SAD:          "😢",
    Emotion.ANGRY:        "😠",
    Emotion.ANNOYED:      "😒",
    Emotion.SHY:          "😳",
    Emotion.TSUNDERE:     "😤",
    Emotion.WORRIED:      "😟",
    Emotion.CUTE_PLAYFUL: "🥰",
    Emotion.TEASING:      "😏",
    Emotion.FLIRTY:       "😉",
    Emotion.WHISPER:      "🤫",
    Emotion.BORED:        "😑",
    Emotion.EXCITED:      "🤩",
}


class ActionType(str, Enum):
    # Image
    SEND_IMAGE     = "send_image"
    GENERATE_IMAGE = "generate_image"
    # Search & info
    WEB_SEARCH     = "web_search"
    WIKIPEDIA      = "wikipedia"
    NEWS           = "news"
    YOUTUBE        = "youtube"
    # Utilities
    WEATHER        = "weather"
    CALCULATOR     = "calculator"
    TRANSLATE      = "translate"
    MAPS           = "maps"
    # Group
    REPLY_TO       = "reply_to"


@dataclass
class Action:
    type: ActionType
    params: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


@dataclass
class ParsedResponse:
    emotion: Emotion
    actions: list[Action]          # ← now a LIST (multi-action support)
    text: str

    # compat shim so old code that reads .action still works
    @property
    def action(self) -> Optional[Action]:
        return self.actions[0] if self.actions else None

    @property
    def emoji(self) -> str:
        return EMOTION_EMOJI.get(self.emotion, "")

    @property
    def formatted_text(self) -> str:
        emoji = self.emoji
        return f"{emoji} {self.text}".strip() if emoji else self.text


@dataclass
class ToolResult:
    """Output from a single tool execution."""
    action_type: ActionType
    text: Optional[str] = None
    image_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class BrainResult:
    """Final result returned to Telegram handler."""
    parsed: ParsedResponse
    tool_results: list[ToolResult] = field(default_factory=list)

    # Convenience: first image found across all tool results
    @property
    def image_url(self) -> Optional[str]:
        for tr in self.tool_results:
            if tr.image_url:
                return tr.image_url
        return None

    # All image URLs (for multi-image responses)
    @property
    def image_urls(self) -> list[str]:
        return [tr.image_url for tr in self.tool_results if tr.image_url]

    # Combined text from all text-producing tools
    @property
    def tool_text(self) -> str:
        parts = [tr.text for tr in self.tool_results if tr.text]
        return "\n\n".join(parts)


@dataclass
class UserContext:
    user_id: int
    username: str = ""
    first_name: str = ""
    language_code: str = "en"
    chat_type: str = "private"
