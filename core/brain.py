"""
core/brain.py — Amy's central orchestrator with Agent Loop.

Agent Loop:
  1. LLM decides what tools to call (can request multiple at once)
  2. Tools execute in parallel
  3. Results fed back to LLM for synthesis
  4. LLM produces final answer
  Max iterations: 3 (prevents infinite loops)

Multi-action: LLM can emit several ACTION blocks in one response.
              All are executed concurrently.
"""
import asyncio
import logging
from typing import Optional

from config import settings
import core.conversation as conv
import core.emotion_engine as emotions
from core.parser import parse_response
from memory.manager import MemoryManager
from models import (
    ParsedResponse, ActionType, Action,
    ToolResult, BrainResult, UserContext, Emotion,
)
from personalities import FEW_SHOT_EXAMPLES
from utils.datetime_utils import get_datetime_in_words
import llm

logger = logging.getLogger("amy.brain")

MAX_AGENT_ITERATIONS = 3   # safety cap on tool loops

# Seeded few-shot content, so should_engage's "recent messages" summary can
# skip them — they're personality demonstrations, not things anyone actually
# just said in the chat.
_FEW_SHOT_CONTENTS = {m["content"] for m in FEW_SHOT_EXAMPLES}


class Brain:
    def __init__(self, memory: MemoryManager):
        self.memory = memory

    # ══════════════════════════════════════════════════════
    #  Public entry points
    # ══════════════════════════════════════════════════════

    async def process(
        self,
        ctx: UserContext,
        user_text: str,
        sender_name: str = "",
        reply_context: str = "",
        spontaneous: bool = False,
    ) -> BrainResult:
        """
        sender_name   — who said this (tags the turn; matters in groups).
        reply_context — e.g. "[In reply to Ali: 'blah']" prepended for the LLM,
                        kept out of `user_text` itself (which tools still see clean).
        spontaneous   — True when Amy is jumping in on her own, not because she
                        was addressed. Changes how the turn is framed for the LLM.
        """
        uid  = ctx.user_id
        cid  = ctx.chat_id or uid
        lang = "fa" if _is_persian(user_text) else "en"

        # 1. Memory (long-term memory stays per-person even in a shared chat)
        memories = self.memory.retrieve_relevant(uid, user_text)
        timestamp = get_datetime_in_words()
        body = f"{reply_context}\n{user_text}" if reply_context else user_text
        contextual = (
            f"--- MEMORY ---\n{memories}\n--------------\n{timestamp} - {body}"
        ) if memories else f"{timestamp} - {body}"

        if spontaneous:
            contextual += (
                "\n\n[No one addressed you directly — you noticed this in the group "
                "and decided to jump in on your own. Keep it short and natural, like "
                "a real person casually chiming in. Only comment on what's actually "
                "relevant or funny.]"
            )

        conv.append(cid, "user", contextual, sender_name=sender_name)

        # 2. Agent loop
        result = await self._agent_loop(ctx, user_text, lang)

        # 3. Non-blocking memory save
        asyncio.create_task(
            self._save_memory(uid, user_text, result.parsed.text)
        )
        return result

    async def should_engage(self, ctx: UserContext, sender_name: str, trigger_text: str) -> bool:
        """
        Cheap yes/no gate for passive group listening, run on the small/fast
        model so it never eats into the main personality model's quota.
        Looks at a little recent shared history for context.
        """
        cid = ctx.chat_id
        recent = conv.snapshot(cid)[-12:]
        recent_text = "\n".join(
            f"- {m['content']}" for m in recent
            if m["role"] == "user" and m["content"] not in _FEW_SHOT_CONTENTS
        ) or "(nothing yet)"

        prompt = (
            "You are a filter deciding whether Amy — a witty, tsundere group-chat "
            "persona — should spontaneously jump into a group conversation, without "
            "being addressed or replied to. She should do this rarely: only when "
            "there is a genuinely funny reaction, a callback to something said "
            "earlier, or something clearly worth teasing someone about. For routine, "
            "boring, or unrelated chatter, she should stay quiet.\n\n"
            f"Recent messages:\n{recent_text}\n\n"
            f"Newest message — {sender_name or 'someone'}: {trigger_text}\n\n"
            "Should Amy jump in right now? Answer with exactly one word: yes or no."
        )
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None, llm.fast_chat, [{"role": "user", "content": prompt}]
        )
        decision = raw.strip().lower().startswith("y")
        logger.info("should_engage(chat=%s): %s -> %s", cid, raw.strip()[:20], decision)
        return decision

    def observe(self, ctx: UserContext, sender_name: str, text: str) -> None:
        """
        Passively records a group message Amy isn't responding to, so later
        (triggered or spontaneous) replies can still reference it.
        """
        cid = ctx.chat_id
        if not cid or not text:
            return
        conv.append(cid, "user", text, sender_name=sender_name)
        conv.trim(cid)

    async def process_image(
        self,
        ctx: UserContext,
        image_b64: str,
        question: str = "",
        sender_name: str = "",
        spontaneous: bool = False,
        media_kind: str = "image",
    ) -> BrainResult:
        """
        Vision: the vision model answers/describes first, then Amy's
        personality is layered on top.

        `media_kind` — "image" | "sticker" | "gif" | "video" — only affects
        the wording of the prompt (e.g. "sent a sticker" vs "sent an image").
        `spontaneous` — True for a media message Amy is reacting to on her own
        (e.g. a GIF nobody addressed her with) — mirrors the group-jump-in
        framing used in process().
        """
        cid  = ctx.chat_id or ctx.user_id
        lang = "fa" if _is_persian(question) else "en"

        loop = asyncio.get_event_loop()

        # Vision model answers/describes the frame
        vision_answer = await loop.run_in_executor(
            None, llm.vision_ask, image_b64, question or "", lang
        )
        logger.info("Vision answer: %s...", vision_answer[:80])

        noun = {"sticker": "a sticker", "gif": "a GIF", "video": "a video"}.get(media_kind, "an image")

        # Build prompt for Amy to react/wrap in personality
        if question:
            prompt = (
                f"The user sent {noun} and asked: '{question}'\n\n"
                f"You analyzed it and found:\n{vision_answer}\n\n"
                "Respond as Amy: give the answer naturally in your personality. "
                "Be direct and specific. If it's a car — name it. "
                "If it's code — explain the bug. Keep it concise."
            )
        else:
            prompt = (
                f"The user sent you {noun}. You see:\n{vision_answer}\n\n"
                "React to it naturally in your personality. Keep it short."
            )

        if spontaneous:
            prompt += (
                "\n\n[Nobody addressed you — you're reacting to this on your own, "
                "like a real group member spontaneously commenting or making a "
                "joke/callback. Keep it very short and only reply if it's actually "
                "worth reacting to.]"
            )

        conv.append(cid, "user", prompt, sender_name=sender_name)
        history = conv.snapshot(cid)
        raw = await loop.run_in_executor(None, llm.chat, history)
        parsed = parse_response(raw)

        conv.append(cid, "assistant", raw)
        conv.trim(cid)
        emotions.update_state(cid, parsed.emotion)

        return BrainResult(parsed=parsed)

    async def process_document(
        self,
        ctx: UserContext,
        file_bytes: bytes,
        filename: str,
        question: str = "",
        sender_name: str = "",
    ) -> BrainResult:
        from tools.file_reader import extract_text, describe_file

        uid  = ctx.user_id
        cid  = ctx.chat_id or uid
        loop = asyncio.get_event_loop()
        fa   = _is_persian(question + filename)

        try:
            text = await loop.run_in_executor(None, extract_text, file_bytes, filename)
        except ValueError as e:
            logger.warning("File extraction failed '%s': %s", filename, e)
            return BrainResult(
                parsed=ParsedResponse(
                    emotion=Emotion.WORRIED, actions=[],
                    text=f"نتونستم فایل رو بخونم: {e}" if fa
                         else f"Couldn't read this file: {e}",
                )
            )

        meta = describe_file(filename, text)
        logger.info("Document: %s", meta)

        if question:
            prompt = (
                f"The user sent you a file.\n{meta}\n\n"
                f"=== FILE CONTENT ===\n{text}\n=== END ===\n\n"
                f"User's question: '{question}'\n\n"
                "Answer using file content. Stay in Amy's personality."
            )
        else:
            prompt = (
                f"The user sent you a file with no question.\n{meta}\n\n"
                f"=== FILE CONTENT ===\n{text}\n=== END ===\n\n"
                "Give a brief summary and suggest questions they could ask. "
                "Stay in character."
            )

        conv.append(cid, "user", prompt, sender_name=sender_name)
        history = conv.snapshot(cid)
        raw = await loop.run_in_executor(None, llm.chat, history, 1000)
        parsed = parse_response(raw)

        conv.append(cid, "assistant", raw)
        conv.trim(cid)
        emotions.update_state(cid, parsed.emotion)
        asyncio.create_task(
            self._save_memory(uid, f"[file:{filename}] {question}", parsed.text)
        )
        return BrainResult(parsed=parsed)

    # ══════════════════════════════════════════════════════
    #  Agent Loop
    # ══════════════════════════════════════════════════════

    async def _agent_loop(
        self,
        ctx: UserContext,
        original_text: str,
        lang: str,
    ) -> BrainResult:
        """
        Iterative tool-use loop:
          LLM → parse → execute tools → feed results back → LLM → ...
        Stops when:
          - LLM returns no actions (final answer)
          - Max iterations reached
        """
        cid           = ctx.chat_id or ctx.user_id
        loop          = asyncio.get_event_loop()
        all_tool_results: list[ToolResult] = []
        final_parsed: Optional[ParsedResponse] = None

        for iteration in range(MAX_AGENT_ITERATIONS):
            history = conv.snapshot(cid)
            raw     = await loop.run_in_executor(None, llm.chat, history)
            logger.debug("Agent iter %d LLM raw:\n%s", iteration, raw[:200])

            parsed = parse_response(raw)
            emotions.update_state(cid, parsed.emotion, original_text)

            # No actions → final answer
            if not parsed.actions:
                conv.append(cid, "assistant", raw)
                conv.trim(cid)
                final_parsed = parsed
                break

            # Execute all actions concurrently
            logger.info(
                "Agent iter %d: executing %d action(s): %s",
                iteration,
                len(parsed.actions),
                [a.type for a in parsed.actions],
            )
            tool_results = await asyncio.gather(
                *[self._execute_single_action(a, original_text, lang) for a in parsed.actions],
                return_exceptions=False,
            )
            all_tool_results.extend(tool_results)

            # Inject tool results back into conversation
            tool_summary = _format_tool_results(tool_results)
            conv.append(cid, "assistant", raw)
            conv.append(
                cid, "user",
                f"[TOOL RESULTS]\n{tool_summary}\n[/TOOL RESULTS]\n\n"
                "Now give your final response to the user based on these results. "
                "Stay in character as Amy.",
            )
            final_parsed = parsed  # keep last parsed in case loop ends here

        else:
            # Max iterations — use last parsed
            logger.warning("Agent loop hit max iterations for chat=%s", cid)
            history = conv.snapshot(cid)
            raw     = await loop.run_in_executor(None, llm.chat, history)
            final_parsed = parse_response(raw)
            conv.append(cid, "assistant", raw)
            conv.trim(cid)

        return BrainResult(
            parsed=final_parsed or ParsedResponse(
                emotion=Emotion.WORRIED, actions=[], text="Something went wrong."
            ),
            tool_results=all_tool_results,
        )

    # ══════════════════════════════════════════════════════
    #  Single tool executor
    # ══════════════════════════════════════════════════════

    async def _execute_single_action(
        self,
        action: Action,
        user_text: str,
        lang: str,
    ) -> ToolResult:
        from tools import (
            web_search, image_search, image_generate,
            translate, weather, calculator, wikipedia,
            news, maps, youtube,
        )

        atype = action.type
        logger.info("Tool: %s | params: %s", atype, action.params)

        try:
            # ── Images ────────────────────────────────────
            if atype == ActionType.SEND_IMAGE:
                query = action.get("query", user_text)
                url   = await image_search.get_url(query)
                if not url and len(query.split()) > 1:
                    url = await image_search.get_url(" ".join(query.split()[1:]))
                if url:
                    return ToolResult(action_type=atype, image_url=url)
                return ToolResult(
                    action_type=atype,
                    text="نتونستم عکسی پیدا کنم." if lang == "fa"
                         else "No image found for that.",
                )

            elif atype == ActionType.GENERATE_IMAGE:
                prompt = action.get("prompt", "")
                if not prompt:
                    return ToolResult(action_type=atype, error="Empty prompt")
                url = await image_generate.generate(prompt)
                if url:
                    return ToolResult(action_type=atype, image_url=url)
                return ToolResult(action_type=atype, error="Image generation failed")

            # ── Web search ────────────────────────────────
            elif atype == ActionType.WEB_SEARCH:
                query   = action.get("query", user_text)
                results = await web_search.search(query)
                return ToolResult(action_type=atype, text=f"[Web search: {query}]\n{results}")

            # ── Wikipedia ─────────────────────────────────
            elif atype == ActionType.WIKIPEDIA:
                query = action.get("query", user_text)
                result = await wikipedia.search_and_summarize(query, lang=lang)
                return ToolResult(action_type=atype, text=result)

            # ── News ──────────────────────────────────────
            elif atype == ActionType.NEWS:
                query    = action.get("query", "")
                category = action.get("category", "general")
                result   = await news.get_news(query, category, lang)
                return ToolResult(action_type=atype, text=result)

            # ── Weather ───────────────────────────────────
            elif atype == ActionType.WEATHER:
                city   = action.get("city", user_text)
                result = await weather.get_weather(city)
                return ToolResult(action_type=atype, text=result)

            # ── Calculator ────────────────────────────────
            elif atype == ActionType.CALCULATOR:
                expr   = action.get("expression", user_text)
                result = calculator.calculate(expr)
                return ToolResult(action_type=atype, text=result)

            # ── Translate ─────────────────────────────────
            elif atype == ActionType.TRANSLATE:
                text_to_translate = action.get("text", user_text)
                target            = action.get("target", "en")
                source            = action.get("source", "auto")
                result = await translate.translate(text_to_translate, target, source)
                return ToolResult(
                    action_type=atype,
                    text=f"Translation ({source} → {target}):\n{result}",
                )

            # ── Maps ──────────────────────────────────────
            elif atype == ActionType.MAPS:
                location = action.get("location", user_text)
                result   = await maps.get_map_link(location)
                return ToolResult(action_type=atype, text=result)

            # ── YouTube ───────────────────────────────────
            elif atype == ActionType.YOUTUBE:
                query = action.get("query", user_text)
                # Auto-translate query if not English
                if any(ord(c) > 127 for c in query):
                    from tools.translate import to_english
                    query = await to_english(query)
                result = await youtube.search_youtube(query)
                return ToolResult(action_type=atype, text=result)

            # ── Reply to ──────────────────────────────────
            elif atype == ActionType.REPLY_TO:
                return ToolResult(action_type=atype)   # handled by Telegram layer

            # ── Moderation ────────────────────────────────
            # Deliberately carries NO target from the LLM's params — the
            # Telegram layer always applies this to whoever actually sent
            # the message being processed, never a name the model wrote.
            elif atype in (ActionType.MUTE_USER, ActionType.BAN_USER):
                if not settings.moderation_enabled:
                    return ToolResult(action_type=atype, error="Moderation disabled")
                verb = "muted" if atype == ActionType.MUTE_USER else "banned"
                return ToolResult(
                    action_type=atype, params=action.params, internal_only=True,
                    text=f"You {verb} the person who sent that message. "
                         "(The actual Telegram action is applied separately.)",
                )

            else:
                logger.warning("Unknown action: %s", atype)
                return ToolResult(action_type=atype, error=f"Unknown tool: {atype}")

        except Exception as e:
            logger.error("Tool %s crashed: %s", atype, e, exc_info=True)
            return ToolResult(action_type=atype, error=str(e))

    # ══════════════════════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════════════════════

    async def _save_memory(self, uid: int, user_in: str, ai_out: str) -> None:
        try:
            self.memory.maybe_save(uid, user_in, ai_out)
        except Exception as e:
            logger.error("Memory save failed: %s", e)


def _format_tool_results(results: list[ToolResult]) -> str:
    parts = []
    for tr in results:
        if tr.error:
            parts.append(f"[{tr.action_type}] ERROR: {tr.error}")
        elif tr.image_url:
            parts.append(f"[{tr.action_type}] Image ready: {tr.image_url}")
        elif tr.text:
            parts.append(f"[{tr.action_type}]\n{tr.text}")
    return "\n\n".join(parts) if parts else "No results."


def _is_persian(text: str) -> bool:
    if not text:
        return False
    return sum(1 for c in text if "\u0600" <= c <= "\u06FF") > len(text) * 0.15
