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

    # ── LLM (NVIDIA NIM) ──────────────────────────────────
    nvidia_api_key: str = field(
        default_factory=lambda: os.environ.get("NVIDIA_API_KEY", "")
    )
    llm_max_tokens: int = 400
    llm_temperature: float = 0.7

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
        if not self.nvidia_api_key:
            errors.append("NVIDIA_API_KEY is not set (get it free at build.nvidia.com)")
        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))


settings = Settings()
