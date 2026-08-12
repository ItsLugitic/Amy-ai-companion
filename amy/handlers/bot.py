"""
telegram/bot.py — Telegram application + all message handlers.

Message routing:
  Private            → always respond (unchanged from before).
  Group, addressed   → mention / reply-to-Amy → normal triggered response.
  Group, NOT addressed → passive path:
      1. Cheap checks (length, cooldown, random precheck) — most messages
         are skipped here for free.
      2. If passed, ask the small/fast model "should I jump in?"
      3. If yes → respond WITHOUT quoting (plain message, like a real
         group member chiming in). If no → just store the message in the
         shared group history so it's available as context later.

Requires privacy mode OFF for this bot in @BotFather (/setprivacy → Disable),
otherwise Telegram never forwards messages that don't address the bot.
"""
import logging
import base64
import random
import time
from datetime import datetime, timedelta, timezone
from telegram import Update, Message, ChatPermissions
from telegram.error import BadRequest, Forbidden
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
import core.moderation as moderation
from memory import MemoryManager
from models import UserContext, ActionType, BrainResult, ToolResult
import utils.media as media

logger = logging.getLogger("amy.telegram")

MAX_FILE_BYTES = 20 * 1024 * 1024   # 20 MB

# Statuses that make a group member untouchable by Amy's moderation tools,
# no matter what the LLM decides — this is enforced here, not by the prompt.
_PROTECTED_STATUSES = {"creator", "administrator"}

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
        self._last_spontaneous: dict[int, float] = {}   # chat_id → monotonic time

    def _build_app(self):
        app = ApplicationBuilder().token(settings.telegram_token).build()
        app.add_handler(CommandHandler("start",  self._start))
        app.add_handler(CommandHandler("reset",  self._reset))
        app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.CAPTION
                 | filters.Document.ALL | filters.Sticker.ALL
                 | filters.ANIMATION | filters.VIDEO) & ~filters.COMMAND,
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
        logger.info("Amy v5 running...")
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
            chat_id=update.effective_chat.id,
        )

    def _sender_name(self, update: Update) -> str:
        u = update.effective_user
        if not u:
            return ""
        return u.first_name or u.username or str(u.id)

    def _reply_context_str(self, msg: Message) -> str:
        """
        Builds "[In reply to Name: 'snippet']" when this message replies to
        someone OTHER than Amy — gives the LLM the thread it's actually in.
        """
        r = msg.reply_to_message
        if not r or (r.from_user and r.from_user.id == self._me_id):
            return ""
        name = (r.from_user.first_name if r.from_user else None) or "someone"
        snippet = (r.text or r.caption or "").strip()
        if not snippet:
            if r.photo:        snippet = "[photo]"
            elif r.sticker:     snippet = "[sticker]"
            elif r.animation:   snippet = "[gif]"
            elif r.video:       snippet = "[video]"
            elif r.document:    snippet = "[file]"
            else:               snippet = "[message]"
        snippet = snippet[:150]
        return f"[In reply to {name}: '{snippet}']"

    def _media_cue(self, msg: Message, media_kind: str) -> str:
        """Cheap metadata-only description used for the should_engage gate — no download/vision spent yet."""
        if media_kind == "sticker" and msg.sticker and msg.sticker.emoji:
            return f"[sent a sticker {msg.sticker.emoji}]"
        return {
            "sticker": "[sent a sticker]",
            "gif":     "[sent a GIF]",
            "video":   "[sent a video]",
        }.get(media_kind, "[sent media]")

    # ── Passive-engagement bookkeeping ─────────────────────

    def _cooldown_ok(self, chat_id: int) -> bool:
        last = self._last_spontaneous.get(chat_id, 0.0)
        return (time.monotonic() - last) >= settings.passive_cooldown_seconds

    def _mark_spontaneous(self, chat_id: int) -> None:
        self._last_spontaneous[chat_id] = time.monotonic()

    # ── Downloaders ────────────────────────────────────────

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

    async def _dl_media_frame_b64(self, msg: Message, media_kind: str) -> str | None:
        """
        Returns a base64 JPEG frame for sticker/gif/video, or None if the
        format can't be rasterized (classic .tgs vector stickers).
        """
        if media_kind == "sticker":
            sticker = msg.sticker
            if sticker.is_animated:      # .tgs — vector Lottie, can't rasterize
                return None
            f = await sticker.get_file()
            b = bytes(await f.download_as_bytearray())
            if sticker.is_video:         # .webm video sticker
                return media.video_bytes_to_frame_b64(b, suffix=".webm")
            return media.webp_bytes_to_jpeg_b64(b)

        if media_kind == "gif":
            anim = msg.animation
            if anim.file_size and anim.file_size > MAX_FILE_BYTES:
                raise ValueError("GIF too large")
            f = await anim.get_file()
            b = bytes(await f.download_as_bytearray())
            return media.video_bytes_to_frame_b64(b, suffix=".mp4")

        if media_kind == "video":
            vid = msg.video
            if vid.file_size and vid.file_size > MAX_FILE_BYTES:
                raise ValueError("Video too large")
            f = await vid.get_file()
            b = bytes(await f.download_as_bytearray())
            return media.video_bytes_to_frame_b64(b, suffix=".mp4")

        return None

    # ── Sending (quote = normal Telegram reply-to; quote=False = plain message) ──

    async def _send(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, *,
        text: str | None = None, photo: str | None = None,
        caption: str | None = None, quote: bool = True,
    ):
        chat_id = update.effective_chat.id
        if photo is not None:
            if quote:
                return await update.message.reply_photo(photo=photo, caption=caption)
            return await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
        if quote:
            return await update.message.reply_text(text)
        return await context.bot.send_message(chat_id=chat_id, text=text)

    # ── Commands ──────────────────────────────────────────

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_me(context)
        ctx = self._make_ctx(update)
        from core.conversation import get as get_history
        get_history(ctx.chat_id)
        startup = self.memory.retrieve_latest(ctx.user_id, 3)
        if startup:
            inject_system(ctx.chat_id, f"Memories from previous sessions:\n{startup}")
        await update.message.reply_text(
            "Amy is online.\n"
            "I can chat, search the web, check weather, read news, translate, "
            "find or generate images, calculate, show maps, search YouTube, "
            "read your files (PDF, DOCX, code, spreadsheets), and react to "
            "stickers/GIFs/videos.\n"
            "In groups I'll also jump in on my own sometimes — not that I was "
            "excited about that or anything, baka."
        )

    async def _reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ctx = self._make_ctx(update)
        reset_history(ctx.chat_id)
        await update.message.reply_text("Fine... I'll pretend we just met. Don't make it weird.")

    # ── Main dispatcher ───────────────────────────────────

    async def _dispatch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_me(context)
        msg = update.message
        if not msg:
            return
        if update.effective_user and update.effective_user.id == self._me_id:
            return   # never react to our own messages (bot-to-bot loops)

        is_group      = update.effective_chat.type in ("group", "supergroup")
        has_photo     = bool(msg.photo)
        has_doc       = bool(msg.document)
        has_sticker   = bool(msg.sticker)
        has_animation = bool(msg.animation)
        has_video     = bool(msg.video)
        caption       = (msg.caption or "").strip()
        sender_name   = self._sender_name(update)

        # ── PHOTO ─────────────────────────────────────────
        if has_photo:
            if not is_group:
                await self._handle_photo(update, context, msg, caption)
                return
            if self._is_mentioned(caption, msg.caption_entities) or self._is_reply_to_amy(msg):
                await self._handle_photo(update, context, msg, caption, sender_name=sender_name)
            return

        # ── DOCUMENT ──────────────────────────────────────
        if has_doc:
            if not is_group:
                await self._handle_doc(update, context, msg, caption)
                return
            if self._is_mentioned(caption, msg.caption_entities) or self._is_reply_to_amy(msg):
                await self._handle_doc(update, context, msg, caption, sender_name=sender_name)
            return

        # ── STICKER / GIF / VIDEO ──────────────────────────
        if has_sticker or has_animation or has_video:
            media_kind = "sticker" if has_sticker else ("gif" if has_animation else "video")
            triggered = self._is_mentioned(caption, msg.caption_entities) or self._is_reply_to_amy(msg)
            if not is_group or triggered:
                await self._handle_media(update, context, msg, caption, media_kind, sender_name=sender_name)
                return
            if settings.passive_listening_enabled:
                await self._maybe_passive_media(update, context, msg, media_kind, sender_name)
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

            # Group — addressed to Amy → normal triggered response
            mentioned  = self._is_mentioned(user_text, msg.entities)
            reply_amy  = self._is_reply_to_amy(msg)
            if mentioned or reply_amy:
                clean     = user_text.replace(f"@{self._me_username}", "").strip()
                reply_ctx = self._reply_context_str(msg)
                if self._is_reply_to_photo(msg):
                    await self._handle_text_reply_photo(update, context, clean, sender_name=sender_name)
                elif self._is_reply_to_doc(msg):
                    await self._handle_text_reply_doc(update, context, clean, sender_name=sender_name)
                else:
                    await self._handle_text(
                        update, context, clean,
                        sender_name=sender_name, reply_context=reply_ctx,
                    )
                return

            # Group — NOT addressed to Amy → passive path
            if settings.passive_listening_enabled:
                await self._maybe_passive_text(update, context, msg, user_text, sender_name)

    # ── Passive engagement ─────────────────────────────────

    async def _maybe_passive_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        msg: Message, user_text: str, sender_name: str,
    ) -> None:
        ctx = self._make_ctx(update)
        chat_id = ctx.chat_id

        # Cheap checks first — most messages are skipped here for free.
        skip = (
            len(user_text) < settings.passive_min_text_len
            or not self._cooldown_ok(chat_id)
            or random.random() > settings.passive_precheck_probability
        )
        if skip:
            self.brain.observe(ctx, sender_name, user_text)
            return

        try:
            engage = await self.brain.should_engage(ctx, sender_name, user_text)
        except Exception as e:
            logger.error("should_engage (text) failed: %s", e)
            engage = False

        if not engage:
            self.brain.observe(ctx, sender_name, user_text)
            return

        self._mark_spontaneous(chat_id)
        reply_ctx = self._reply_context_str(msg)
        await self._handle_text(
            update, context, user_text,
            sender_name=sender_name, reply_context=reply_ctx,
            spontaneous=True, quote=False,
        )

    async def _maybe_passive_media(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        msg: Message, media_kind: str, sender_name: str,
    ) -> None:
        ctx = self._make_ctx(update)
        chat_id = ctx.chat_id
        cue = self._media_cue(msg, media_kind)

        skip = (
            not self._cooldown_ok(chat_id)
            or random.random() > settings.passive_precheck_probability
        )
        if skip:
            self.brain.observe(ctx, sender_name, cue)
            return

        try:
            engage = await self.brain.should_engage(ctx, sender_name, cue)
        except Exception as e:
            logger.error("should_engage (media) failed: %s", e)
            engage = False

        if not engage:
            self.brain.observe(ctx, sender_name, cue)
            return

        self._mark_spontaneous(chat_id)
        await self._handle_media(
            update, context, msg, "", media_kind,
            sender_name=sender_name, spontaneous=True, quote=False,
        )

    # ── Core handlers ─────────────────────────────────────

    async def _handle_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
        sender_name: str = "", reply_context: str = "",
        spontaneous: bool = False, quote: bool = True,
    ) -> None:
        if not text:
            return
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        result = await self.brain.process(
            ctx, text,
            sender_name=sender_name, reply_context=reply_context, spontaneous=spontaneous,
        )
        await self._deliver(update, context, result, text, quote=quote)

    async def _handle_photo(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        photo_msg: Message,
        question: str,
        sender_name: str = "",
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
        result = await self.brain.process_image(ctx, b64, question, sender_name=sender_name)
        await update.message.reply_text(result.parsed.formatted_text)

    async def _handle_media(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        media_msg: Message,
        question: str,
        media_kind: str,
        sender_name: str = "",
        spontaneous: bool = False,
        quote: bool = True,
    ) -> None:
        """Sticker / GIF / video → one frame → vision → Amy's reaction."""
        ctx = self._make_ctx(update)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        try:
            b64 = await self._dl_media_frame_b64(media_msg, media_kind)
        except Exception as e:
            logger.error("%s download failed: %s", media_kind, e)
            if not spontaneous:
                await self._send(
                    update, context, quote=quote,
                    text=("نتونستم اینو دانلود کنم." if _is_persian(question)
                          else "Couldn't download that."),
                )
            return
        if b64 is None:
            return   # unsupported format (e.g. classic .tgs sticker) — skip quietly
        result = await self.brain.process_image(
            ctx, b64, question,
            sender_name=sender_name, spontaneous=spontaneous, media_kind=media_kind,
        )
        await self._send(update, context, text=result.parsed.formatted_text, quote=quote)

    async def _handle_text_reply_photo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, question: str,
        sender_name: str = "",
    ) -> None:
        replied = update.message.reply_to_message
        if not replied or not replied.photo:
            await self._handle_text(update, context, question, sender_name=sender_name)
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
        result = await self.brain.process_image(ctx, b64, q, sender_name=sender_name)
        await update.message.reply_text(result.parsed.formatted_text)

    async def _handle_doc(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        doc_msg: Message,
        question: str,
        sender_name: str = "",
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
        result = await self.brain.process_document(ctx, file_bytes, filename, question, sender_name=sender_name)
        await self._send_long(update, context, result.parsed.formatted_text)

    async def _handle_text_reply_doc(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, question: str,
        sender_name: str = "",
    ) -> None:
        replied = update.message.reply_to_message
        if not replied or not replied.document:
            await self._handle_text(update, context, question, sender_name=sender_name)
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
        result = await self.brain.process_document(ctx, file_bytes, filename, q, sender_name=sender_name)
        await self._send_long(update, context, result.parsed.formatted_text)

    # ── Moderation ─────────────────────────────────────────

    def _clamp_mute_minutes(self, value) -> int:
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            minutes = settings.mute_default_minutes
        return max(1, min(minutes, settings.mute_max_minutes))

    async def _apply_moderation(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, tr: ToolResult,
    ) -> None:
        """
        Executes a mute/ban decided by the LLM — ALWAYS against whoever
        actually sent the message currently being processed (from the real
        Update object), never against a name the model might have written
        into the action's params. Admins/the group creator are never touched.

        An outright ban only actually happens once the sender has racked up
        `moderation_ban_after_strikes` strikes; a "ban" requested before that
        is downgraded to an escalating mute instead, so one bad call from the
        model can't get someone permanently removed on a first offense.
        A short note is always posted in the group — no silent moderation.
        """
        chat   = update.effective_chat
        target = update.effective_user
        if not settings.moderation_enabled or not target or not chat:
            return
        if chat.type not in ("group", "supergroup"):
            return
        if target.id == self._me_id:
            return

        try:
            member = await context.bot.get_chat_member(chat.id, target.id)
            if member.status in _PROTECTED_STATUSES:
                logger.info("Moderation skipped — %s is %s", target.id, member.status)
                return
        except Exception as e:
            logger.warning("get_chat_member failed for %s: %s", target.id, e)
            return   # fail safe: don't act if we can't verify who they are

        fa = _is_persian((update.message.text or update.message.caption or "") if update.message else "")

        strikes = moderation.add_strike(
            chat.id, target.id, settings.moderation_strike_reset_hours * 3600
        )
        ban_now = strikes >= settings.moderation_ban_after_strikes

        try:
            if tr.action_type == ActionType.BAN_USER and ban_now:
                await context.bot.ban_chat_member(chat.id, target.id)
                logger.info("Banned user %s in chat %s (strike %d)", target.id, chat.id, strikes)
                note = (
                    f"بن شد. اخطار {strikes}م بود، بسه دیگه." if fa
                    else f"Banned. That was strike {strikes} — enough is enough."
                )

            else:
                if tr.action_type == ActionType.MUTE_USER:
                    minutes = self._clamp_mute_minutes((tr.params or {}).get("duration_minutes"))
                else:
                    # a requested ban that got downgraded — not enough strikes yet
                    minutes = settings.moderation_mute_minutes[
                        min(strikes - 1, len(settings.moderation_mute_minutes) - 1)
                    ]
                until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                await context.bot.restrict_chat_member(
                    chat.id, target.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
                logger.info(
                    "Muted user %s in chat %s for %d min (strike %d)",
                    target.id, chat.id, minutes, strikes,
                )
                warning = (
                    ""
                    if strikes < settings.moderation_ban_after_strikes - 1
                    else (" دفعه بعد بن می‌شی." if fa else " Next one gets you banned.")
                )
                note = (
                    f"{minutes} دقیقه ساکت شد. (اخطار {strikes}/{settings.moderation_ban_after_strikes}){warning}"
                    if fa else
                    f"Muted for {minutes} min. (Strike {strikes}/{settings.moderation_ban_after_strikes}){warning}"
                )

            await context.bot.send_message(chat_id=chat.id, text=note)

        except (BadRequest, Forbidden) as e:
            logger.warning("Moderation action %s failed (no admin rights?): %s", tr.action_type, e)
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "می‌خواستم اقدام کنم ولی ادمین نیستم؛ یکی باید بهم دسترسی محدودسازی/بن بده."
                    if fa else
                    "Tried to act on that but I'm not an admin here — someone needs to give me "
                    "restrict/ban permissions."
                ),
            )
        except Exception as e:
            logger.error("Unexpected moderation error (%s): %s", tr.action_type, e)

    # ── Result delivery ───────────────────────────────────

    async def _deliver(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        result: BrainResult,
        user_input: str,
        quote: bool = True,
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

        # ── Moderation (mute/ban) — applied no matter what else the reply does ──
        for tr in result.tool_results:
            if tr.action_type in (ActionType.MUTE_USER, ActionType.BAN_USER) and not tr.error:
                await self._apply_moderation(update, context, tr)

        # ── Images first ──────────────────────────────────────────────────────
        image_urls = result.image_urls
        if image_urls:
            await context.bot.send_chat_action(
                update.effective_chat.id, ChatAction.UPLOAD_PHOTO
            )
            for i, url in enumerate(image_urls):
                cap = amy_text if i == 0 and parsed.text else None
                try:
                    await self._send(update, context, photo=url, caption=cap, quote=quote)
                except Exception as e:
                    logger.error("Failed to send image %s: %s", url[:60], e)
                    await self._send(
                        update, context, quote=quote,
                        text=("نتونستم عکس رو بفرستم." if fa else "Couldn't send the image."),
                    )
            # If there was also tool text (e.g. weather alongside image), send it
            if result.tool_text:
                await self._send_long(update, context, result.tool_text, quote=quote)
            return

        # ── reply_to group action ─────────────────────────────────────────────
        if parsed.action and parsed.action.type == ActionType.REPLY_TO:
            target   = parsed.action.get("target_username", "")
            msg_text = parsed.action.get("text", "")
            if msg_text:
                mention = f"@{target.lstrip('@')} " if target else ""
                body    = mention + msg_text
                full    = f"{amy_text}\n\n{body}" if parsed.text else body
                await self._send(update, context, text=full, quote=quote)
                return

        # ── Tool text results (weather, news, search, etc.) ───────────────────
        parts: list[str] = []
        if amy_text:
            parts.append(amy_text)
        if result.tool_text:
            parts.append(result.tool_text)

        final = "\n\n".join(parts)
        if final:
            await self._send_long(update, context, final, quote=quote)
        elif amy_text:
            await self._send_long(update, context, amy_text, quote=quote)

    async def _send_long(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, quote: bool = True,
    ) -> None:
        """Send text, splitting into ≤4000-char chunks if needed."""
        LIMIT = 4000
        if not text:
            return
        if len(text) <= LIMIT:
            await self._send(update, context, text=text, quote=quote)
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
                await self._send(update, context, text=chunk, quote=quote)


def _is_persian(text: str) -> bool:
    if not text:
        return False
    return sum(1 for c in text if "\u0600" <= c <= "\u06FF") > len(text) * 0.15
