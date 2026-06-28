"""
Amy v3 — Modular Tsundere AI Bot
Entry point
"""
import logging
from config import settings
from handlers.bot import AmyBot

logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-8s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger("amy.main")


def main() -> None:
    settings.validate()
    logger.info("Starting Amy v4...")
    bot = AmyBot()
    bot.run()   # run_polling() manages its own event loop internally


if __name__ == "__main__":
    main()
