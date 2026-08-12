"""
config.py — All settings loaded from environment variables.
"""
import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # ── Telegram ──────────────────────────────────────────
    telegram_token: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_TOKEN", "")
    )

    # ── LLM (Groq) ────────────────────────────────────────
    # Multiple keys, comma-separated — when one hits its rate limit, the
    # client rotates to the next key for the SAME model (never switches
    # models on its own), so personality/output quality stays consistent.
    # GROQ_API_KEY (singular) still works if you only have one key.
    groq_api_keys: list = field(
        default_factory=lambda: [
            k.strip() for k in os.environ.get(
                "GROQ_API_KEYS", os.environ.get("GROQ_API_KEY", "")
            ).split(",") if k.strip()
        ]
    )
    # Main personality/reasoning model — handles real replies.
    groq_chat_model: str = field(
        default_factory=lambda: os.environ.get("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
    )
    # Small/fast model — used only for the cheap "should I jump in?" gate,
    # so it burns its own separate free-tier quota instead of the main model's.
    groq_fast_model: str = field(
        default_factory=lambda: os.environ.get("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
    )
    # Vision-capable model.
    groq_vision_model: str = field(
        default_factory=lambda: os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
    )
    llm_max_tokens: int = 400
    llm_temperature: float = 0.7

    # ── Passive group listening ("jump in on its own") ─────
    # Requires privacy mode OFF for the bot in @BotFather (/setprivacy → Disable),
    # otherwise Telegram never forwards messages that don't address the bot.
    passive_listening_enabled: bool = field(
        default_factory=lambda: os.environ.get("PASSIVE_LISTENING_ENABLED", "true").lower() == "true"
    )
    # Cheap random pre-filter BEFORE even calling the fast model — most messages
    # are skipped here for free, so the fast model is only consulted sometimes.
    passive_precheck_probability: float = field(
        default_factory=lambda: float(os.environ.get("PASSIVE_PRECHECK_PROBABILITY", "0.25"))
    )
    # Minimum seconds between two spontaneous (non-triggered) replies in the same chat.
    passive_cooldown_seconds: int = field(
        default_factory=lambda: int(os.environ.get("PASSIVE_COOLDOWN_SECONDS", "45"))
    )
    # Ignore very short messages ("ok", "lol") for the passive-engagement check.
    passive_min_text_len: int = 6

    # ── Group moderation (mute / ban abusive users) ────────
    # Requires the bot to be a group ADMIN with "Restrict members" and
    # "Ban users" permissions — otherwise these calls just fail (logged,
    # with a short in-character note in the group instead of a silent no-op).
    #
    # Amy decides mute_user/ban_user via the normal ACTION mechanism, but the
    # target is ALWAYS resolved by the bot layer from whoever actually sent
    # the message being processed — never from anything the model writes —
    # and an outright ban only actually happens after repeat offenses
    # (see moderation_ban_after_strikes); a first-offense "ban" gets
    # downgraded to an escalating mute instead, in case of a bad call.
    moderation_enabled: bool = field(
        default_factory=lambda: os.environ.get("MODERATION_ENABLED", "true").lower() == "true"
    )
    # Clamp for a single mute Amy requests directly (minutes).
    mute_default_minutes: int = field(
        default_factory=lambda: int(os.environ.get("MUTE_DEFAULT_MINUTES", "10"))
    )
    mute_max_minutes: int = field(
        default_factory=lambda: int(os.environ.get("MUTE_MAX_MINUTES", "180"))
    )
    # Escalating mute duration (minutes) used per strike when a "ban" gets
    # downgraded — last value repeats for any strike beyond the list length.
    moderation_mute_minutes: list = field(
        default_factory=lambda: [
            int(x) for x in os.environ.get("MODERATION_MUTE_MINUTES", "5,30,180").split(",") if x
        ]
    )
    # A ban is only actually applied once someone reaches this many strikes.
    moderation_ban_after_strikes: int = field(
        default_factory=lambda: int(os.environ.get("MODERATION_BAN_AFTER_STRIKES", "4"))
    )
    # Strikes older than this decay away, so one bad day doesn't follow someone forever.
    moderation_strike_reset_hours: int = field(
        default_factory=lambda: int(os.environ.get("MODERATION_STRIKE_RESET_HOURS", "72"))
    )

    # ── Search ────────────────────────────────────────────
    serpapi_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("SERPAPI_KEY") or None
    )

    # ── Images ────────────────────────────────────────────
    pixabay_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("PIXABAY_API_KEY") or None
    )
    pollinations_base_url: str = "https://image.pollinations.ai/prompt"

    # ── Memory ────────────────────────────────────────────
    chroma_path: str = "./amy_memories"
    chroma_collection: str = "long_term_memory"
    memory_cooldown_seconds: int = 70
    history_max_messages: int = 20

    # ── Personality ───────────────────────────────────────
    bot_name: str = "Amy"
    creator_name: str = "Matin"

    def validate(self) -> None:
        errors = []
        if not self.telegram_token:
            errors.append("TELEGRAM_TOKEN is not set")
        if not self.groq_api_keys:
            errors.append("GROQ_API_KEY (or GROQ_API_KEYS) is not set (get one free at console.groq.com/keys)")
        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))


settings = Settings()
