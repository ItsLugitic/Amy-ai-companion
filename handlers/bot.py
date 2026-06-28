"""
telegram/bot.py — Telegram application + all message handlers.

Message routing:
  TEXT   → private: always; group: only mention/reply-to-Amy
  PHOTO  → private: always; group: mention in caption or reply-to-Amy
  DOC    → private: always; group: mention in caption or reply-to-Amy

  Text reply to photo   → analyze that photo
  Text reply to doc     → re-read that document
  Text reply to Amy msg → normal conversation
"""
import logging
import base64
from telegram import Update, Message
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)
from telegram.constants import ChatAction
from config import settings
from core import Brain, reset_history
from core.conversation import inject_system
from memory import MemoryManager
from models import UserContext, ActionType, BrainResult

logger = logging.getLogger("amy.telegram")

MAX_FILE_BYTES = 20 * 1024 * 1024   # 20 MB

READABLE_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md", ".py", ".js", ".ts", ".go",
    ".json", ".csv", ".xlsx", ".xls", ".yaml", ".yml", ".toml",
    ".ini", ".html", ".htm", ".xml", ".log", ".sh", ".bat",
    ".rst", ".css", ".env", ".c", ".cpp", ".java", ".rs",
    ".rb", ".php", ".swift", ".kt",
}


class AmyBot:
    def __init__(self):
        self.memory = MemoryManager()
        self.brain  = Brain(memory=self.memory)
        self._me_id: int | None = None
        self._me_username: str  = ""

    def _build_app(self):
        app = ApplicationBuilder().token(settings.telegram_token).build()
        app.add_handler(CommandHandler("start",  self._start))
        app.add_handler(CommandHandler("reset",  self._reset))
        app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.CAPTION
                 | filters.Document.ALL) & ~filters.COMMAND,
                self._dispatch,
            )
        )
        return app

    def run(self) -> None:
        """
        run_polling() internally calls asyncio.run() and manages its own event loop.
        This method must NOT be async — wrapping it in asyncio.run() would nest two loops
        and cause RuntimeError: This event loop is already running.
        """
        app = self._build_app()
        logger.info("Amy v4 running...")
        app.run_polling(drop_pending_updates=True)

    # ── Identity helpers ──────────────────────────────────

    async def _ensure_me(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self._me_id is None:
            me = await context.bot.get_me()
            self._me_id       = me.id
            self._me_username = (me.username or "").lower()

    def _is_mentioned(self, text: str | None, entities) -> bool:
        if not self._me_username or not text:
            return False
        if f"@{self._me_username}" in text.lower():
            return True
        for e in (entities or []):
            if (
                e.type == "mention"
                and text[e.offset:e.offset + e.length].lower() == f"@{self._me_username}"
            ):
                return True
        return False

    def _is_reply_to_amy(self, msg: Message) -> bool:
        r = msg.reply_to_message
        return r is not None and r.from_user is not None and r.from_user.id == self._me_id

    def _is_reply_to_photo(self, msg: Message) -> bool:
        r = msg.reply_to_message
        return r is not None and bool(r.photo)

    def _is_reply_to_doc(self, msg: Message) -> bool:
        r = msg.reply_to_message
        return r is not None and r.document is not None

    def _make_ctx(self, update: Update) -> UserContext:
        u = update.effective_user
        return UserContext(
            user_id=u.id,
            username=u.username or "",
            first_name=u.first_name or "",
            language_code=u.language_code or "en",
            chat_type=update.effective_chat.type,
        )

    async def _dl_photo_b64(self, msg: Message) -> str:
        f = await msg.photo[-1].get_file()
        b = await f.download_as_bytearray()
        return base64.b64encode(bytes(b)).decode()

    async def _dl_doc_bytes(self, msg: Message) -> tuple[bytes, str]:
        doc      = msg.document
        filename = doc.file_name or "file"
        ext      = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        fa       = _is_persian(filename)
        if doc.file_size and doc.file_size > MAX_FILE_BYTES:
            mb = doc.file_size // 1024 // 1024
            raise ValueError(
                f"فایل خیلی بزرگه ({mb} MB). حداکثر ۲۰ مگابایت." if fa
                else f"File too large ({mb} MB). Max 20 MB."
            )
        if ext and ext not in READABLE_EXTENSIONS:
            raise ValueError(
                f"این نوع فایل رو نمیتونم بخونم ({ext})." if fa
                else f"Can't read {ext} files. Supported: PDF, DOCX, TXT, CSV, XLSX, code files…"
            )
        tf = await doc.get_file()
        b  = await tf.download_as_bytearray()
        return bytes(b), filename

    # ── Commands ──────────────────────────────────────────

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_me(context)
        uid = update.effective_user.id
        from core.conversation import get as get_history
        get_history(uid)
        startup = self.memory.retrieve_latest(uid, 3)
        if startup:
            inject_system(uid, f"Memories from previous sessions:\n{startup}")
        await update.message.reply_text(
            "Amy is online.\n"
            "I can chat, search the web, check weather, read news, translate, "
            "find or generate images, calculate, show maps, search YouTube, "
            "and read your files (PDF, DOCX, code, spreadsheets).\n"
            "...not that I was excited about that or anything, baka."
        )

    async def _reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        reset_history(update.effective_user.id)
        await update.message.reply_text("Fine... I'll pretend we just met. Don't make it weird.")

    # ── Main dispatcher ───────────────────────────────────

    async def _dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_me(context)
        msg = update.message
        if not msg:
            return

        is_group  = update.effective_chat.type in ("group", "supergroup")
        has_photo = bool(msg.photo)
        has_doc   = bool(msg.document)
        caption   = (msg.caption or "").strip()

        # ── PHOTO ─────────────────────────────────────────
        if has_photo:
            if not is_group:
                await self._handle_photo(update, context, msg, caption)
                return
            if self._is_mentioned(caption, msg.caption_entities) or self._is_reply_to_amy(msg):
                await self._handle_photo(update, context, msg, caption)
            return

        # ── DOCUMENT ──────────────────────────────────────
        if has_doc:
            if not is_group:
                await self._handle_doc(update, context, msg, caption)
                return
            if self._is_mentioned(caption, msg.caption_entities) or self._is_reply_to_amy(msg):
                await self._handle_doc(update, context, msg, caption)
            return

        # ── TEXT ──────────────────────────────────────────
        if msg.text:
            user_text = msg.text.strip()

            if not is_group:
                if self._is_reply_to_photo(msg):
                    await self._handle_text_reply_photo(update, context, user_text)
                elif self._is_reply_to_doc(msg):
                    await self._handle_text_reply_doc(update, context, user_text)
                else:
                    await self._handle_text(update, context, user_text)
                return

            # Group — only if addressed to Amy
            mentioned  = self._is_mentioned(user_text, msg.entities)
            reply_amy  = self._is_reply_to_amy(msg)
            if not mentioned and not reply_amy:
                return

            clean = user_text.replace(f"@{self._me_username}", "").strip()

            if self._is_reply_to_photo(msg):
                await self._handle_text_reply_photo(update, context, clean)
            elif self._is_reply_to_doc(msg):
                await self._handle_text_reply_doc(update, context, clean)
            else:
                await self._handle_text(update, context, clean)

    # ── Core handlers ─────────────────────────────────────

    async def _handle_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        if not text:
            return
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        result = await self.brain.process(ctx, text)
        await self._deliver(update, context, result, text)

    async def _handle_photo(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        photo_msg: Message,
        question: str,
    ) -> None:
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            b64 = await self._dl_photo_b64(photo_msg)
        except Exception as e:
            logger.error("Photo download failed: %s", e)
            await update.message.reply_text(
                "نتونستم عکس رو دانلود کنم." if _is_persian(question)
                else "Couldn't download the photo."
            )
            return
        result = await self.brain.process_image(ctx, b64, question)
        await update.message.reply_text(result.parsed.formatted_text)

    async def _handle_text_reply_photo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, question: str
    ) -> None:
        replied = update.message.reply_to_message
        if not replied or not replied.photo:
            await self._handle_text(update, context, question)
            return
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            b64 = await self._dl_photo_b64(replied)
        except Exception as e:
            logger.error("Replied photo download failed: %s", e)
            await update.message.reply_text(
                "نتونستم عکس رو بخونم." if _is_persian(question) else "Couldn't read that photo."
            )
            return
        q = question or (
            "این عکس رو توضیح بده" if _is_persian(update.message.text or "")
            else "Describe and analyze this image."
        )
        result = await self.brain.process_image(ctx, b64, q)
        await update.message.reply_text(result.parsed.formatted_text)

    async def _handle_doc(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        doc_msg: Message,
        question: str,
    ) -> None:
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            file_bytes, filename = await self._dl_doc_bytes(doc_msg)
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
        except Exception as e:
            logger.error("Doc download failed: %s", e)
            await update.message.reply_text(
                "نتونستم فایل رو دانلود کنم." if _is_persian(question)
                else "Couldn't download the file."
            )
            return
        logger.info("Processing doc '%s' (%d bytes)", filename, len(file_bytes))
        result = await self.brain.process_document(ctx, file_bytes, filename, question)
        await self._send_long(update, result.parsed.formatted_text)

    async def _handle_text_reply_doc(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, question: str
    ) -> None:
        replied = update.message.reply_to_message
        if not replied or not replied.document:
            await self._handle_text(update, context, question)
            return
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            file_bytes, filename = await self._dl_doc_bytes(replied)
        except ValueError as e:
            await update.message.reply_text(str(e))
            return
        except Exception as e:
            logger.error("Replied doc download failed: %s", e)
            await update.message.reply_text(
                "نتونستم فایل رو دوباره بخونم." if _is_persian(question)
                else "Couldn't re-read the file."
            )
            return
        q = question or (
            "خلاصه‌اش کن" if _is_persian(update.message.text or "")
            else "Summarize the content of this file."
        )
        result = await self.brain.process_document(ctx, file_bytes, filename, q)
        await self._send_long(update, result.parsed.formatted_text)

    # ── Result delivery ───────────────────────────────────

    async def _deliver(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        result: BrainResult,
        user_input: str,
    ) -> None:
        """
        Delivers a BrainResult to the user.
        Handles:
          - Multiple images (one by one)
          - Text tool results (combined)
          - reply_to group action
          - Plain text response
        """
        parsed  = result.parsed
        amy_text = parsed.formatted_text    # emoji + personality text
        fa       = _is_persian(user_input)

        # ── Images first ──────────────────────────────────────────────────────
        image_urls = result.image_urls
        if image_urls:
            await context.bot.send_chat_action(
                update.effective_chat.id, ChatAction.UPLOAD_PHOTO
            )
            for i, url in enumerate(image_urls):
                cap = amy_text if i == 0 and parsed.text else None
                try:
                    await update.message.reply_photo(photo=url, caption=cap)
                except Exception as e:
                    logger.error("Failed to send image %s: %s", url[:60], e)
                    await update.message.reply_text(
                        "نتونستم عکس رو بفرستم." if fa else "Couldn't send the image."
                    )
            # If there was also tool text (e.g. weather alongside image), send it
            if result.tool_text:
                await self._send_long(update, result.tool_text)
            return

        # ── reply_to group action ─────────────────────────────────────────────
        if parsed.action and parsed.action.type == ActionType.REPLY_TO:
            target   = parsed.action.get("target_username", "")
            msg_text = parsed.action.get("text", "")
            if msg_text:
                mention = f"@{target.lstrip('@')} " if target else ""
                body    = mention + msg_text
                full    = f"{amy_text}\n\n{body}" if parsed.text else body
                await update.message.reply_text(full)
                return

        # ── Tool text results (weather, news, search, etc.) ───────────────────
        parts: list[str] = []
        if amy_text:
            parts.append(amy_text)
        if result.tool_text:
            parts.append(result.tool_text)

        final = "\n\n".join(parts)
        if final:
            await self._send_long(update, final)
        elif amy_text:
            await self._send_long(update, amy_text)

    async def _send_long(self, update: Update, text: str) -> None:
        """Send text, splitting into ≤4000-char chunks if needed."""
        LIMIT = 4000
        if not text:
            return
        if len(text) <= LIMIT:
            await update.message.reply_text(text)
            return

        chunks: list[str] = []
        current = ""
        for para in text.split("\n\n"):
            if len(current) + len(para) + 2 <= LIMIT:
                current += ("" if not current else "\n\n") + para
            else:
                if current:
                    chunks.append(current)
                if len(para) > LIMIT:
                    for i in range(0, len(para), LIMIT):
                        chunks.append(para[i:i + LIMIT])
                else:
                    current = para
        if current:
            chunks.append(current)

        for chunk in chunks:
            if chunk:
                await update.message.reply_text(chunk)


def _is_persian(text: str) -> bool:
    if not text:
        return False
    return sum(1 for c in text if "\u0600" <= c <= "\u06FF") > len(text) * 0.15
