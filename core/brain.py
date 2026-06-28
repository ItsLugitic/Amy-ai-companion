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

import core.conversation as conv
import core.emotion_engine as emotions
from core.parser import parse_response
from memory.manager import MemoryManager
from models import (
    ParsedResponse, ActionType, Action,
    ToolResult, BrainResult, UserContext, Emotion,
)
from utils.datetime_utils import get_datetime_in_words
import llm

logger = logging.getLogger("amy.brain")

MAX_AGENT_ITERATIONS = 3   # safety cap on tool loops


class Brain:
    def __init__(self, memory: MemoryManager):
        self.memory = memory

    # ══════════════════════════════════════════════════════
    #  Public entry points
    # ══════════════════════════════════════════════════════

    async def process(self, ctx: UserContext, user_text: str) -> BrainResult:
        uid  = ctx.user_id
        lang = "fa" if _is_persian(user_text) else "en"

        # 1. Memory
        memories = self.memory.retrieve_relevant(uid, user_text)
        timestamp = get_datetime_in_words()
        contextual = (
            f"--- MEMORY ---\n{memories}\n--------------\n{timestamp} - {user_text}"
        ) if memories else f"{timestamp} - {user_text}"

        conv.append(uid, "user", contextual)

        # 2. Agent loop
        result = await self._agent_loop(ctx, user_text, lang)

        # 3. Non-blocking memory save
        asyncio.create_task(
            self._save_memory(uid, user_text, result.parsed.text)
        )
        return result

    async def process_image(
        self,
        ctx: UserContext,
        image_b64: str,
        question: str = "",
    ) -> BrainResult:
        """
        Vision: the vision model answers the question directly.
        Amy's personality is then layered on top.
        """
        uid  = ctx.user_id
        lang = "fa" if _is_persian(question) else "en"

        loop = asyncio.get_event_loop()

        # Vision model answers the actual question
        vision_answer = await loop.run_in_executor(
            None, llm.vision_ask, image_b64, question or "", lang
        )
        logger.info("Vision answer: %s...", vision_answer[:80])

        # Build prompt for Amy to react/wrap in personality
        if question:
            prompt = (
                f"The user sent an image and asked: '{question}'\n\n"
                f"You analyzed the image and found:\n{vision_answer}\n\n"
                "Respond as Amy: give the answer naturally in your personality. "
                "Be direct and specific. If it's a car — name it. "
                "If it's code — explain the bug. Keep it concise."
            )
        else:
            prompt = (
                f"The user sent you an image. You see:\n{vision_answer}\n\n"
                "React to this image naturally in your personality. Keep it short."
            )

        conv.append(uid, "user", prompt)
        history = conv.snapshot(uid)
        raw = await loop.run_in_executor(None, llm.chat, history)
        parsed = parse_response(raw)

        conv.append(uid, "assistant", raw)
        conv.trim(uid)
        emotions.update_state(uid, parsed.emotion)

        return BrainResult(parsed=parsed)

    async def process_document(
        self,
        ctx: UserContext,
        file_bytes: bytes,
        filename: str,
        question: str = "",
    ) -> BrainResult:
        from tools.file_reader import extract_text, describe_file

        uid  = ctx.user_id
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

        conv.append(uid, "user", prompt)
        history = conv.snapshot(uid)
        raw = await loop.run_in_executor(None, llm.chat, history, 1000)
        parsed = parse_response(raw)

        conv.append(uid, "assistant", raw)
        conv.trim(uid)
        emotions.update_state(uid, parsed.emotion)
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
        uid           = ctx.user_id
        loop          = asyncio.get_event_loop()
        all_tool_results: list[ToolResult] = []
        final_parsed: Optional[ParsedResponse] = None

        for iteration in range(MAX_AGENT_ITERATIONS):
            history = conv.snapshot(uid)
            raw     = await loop.run_in_executor(None, llm.chat, history)
            logger.debug("Agent iter %d LLM raw:\n%s", iteration, raw[:200])

            parsed = parse_response(raw)
            emotions.update_state(uid, parsed.emotion, original_text)

            # No actions → final answer
            if not parsed.actions:
                conv.append(uid, "assistant", raw)
                conv.trim(uid)
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
            conv.append(uid, "assistant", raw)
            conv.append(
                uid, "user",
                f"[TOOL RESULTS]\n{tool_summary}\n[/TOOL RESULTS]\n\n"
                "Now give your final response to the user based on these results. "
                "Stay in character as Amy.",
            )
            final_parsed = parsed  # keep last parsed in case loop ends here

        else:
            # Max iterations — use last parsed
            logger.warning("Agent loop hit max iterations for uid=%d", uid)
            history = conv.snapshot(uid)
            raw     = await loop.run_in_executor(None, llm.chat, history)
            final_parsed = parse_response(raw)
            conv.append(uid, "assistant", raw)
            conv.trim(uid)

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
