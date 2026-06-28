"""
core/parser.py — Parses raw LLM output into ParsedResponse.

Supports:
  - Multiple ACTION blocks in one response (multi-action)
  - Robust regex matching (ACTION on same line as text, lowercase, etc.)
  - Single-quote JSON fix
"""
import json
import re
import logging
from models import Emotion, Action, ActionType, ParsedResponse

logger = logging.getLogger("amy.parser")

# Matches ACTION:{...} anywhere, case-insensitive, non-greedy on JSON object
_ACTION_RE = re.compile(r'ACTION\s*:\s*(\{[^}]+\})', re.IGNORECASE)


def parse_response(raw: str) -> ParsedResponse:
    if not raw or not raw.strip():
        logger.warning("LLM returned empty response")
        return ParsedResponse(emotion=Emotion.NEUTRAL, actions=[], text="...")

    lines     = raw.strip().splitlines()
    emotion   = Emotion.NEUTRAL
    actions: list[Action] = []
    start_idx = 0

    # ── Emotion from first line ────────────────────────────────────────────────
    if lines and lines[0].strip().lower().startswith("emotion:"):
        raw_emotion = lines[0].split(":", 1)[1].strip().lower()
        emotion     = Emotion.from_str(raw_emotion)
        start_idx   = 1

    remaining = "\n".join(lines[start_idx:])

    # ── Find ALL ACTION blocks ─────────────────────────────────────────────────
    for match in _ACTION_RE.finditer(remaining):
        raw_json = match.group(1).strip()
        try:
            fixed = raw_json.replace("'", '"')
            data  = json.loads(fixed)
            atype_str = data.pop("type", None)
            if atype_str:
                try:
                    action = Action(type=ActionType(atype_str), params=data)
                    actions.append(action)
                    logger.info("Parsed action: %s %s", atype_str, data)
                except ValueError:
                    logger.warning("Unknown action type: '%s'", atype_str)
        except json.JSONDecodeError as e:
            logger.error("ACTION JSON parse failed: %s | raw: %s", e, raw_json[:80])

    # ── Clean text (remove all ACTION blocks) ─────────────────────────────────
    clean = _ACTION_RE.sub("", remaining).strip()
    text_lines = [l.strip() for l in clean.splitlines() if l.strip()]
    final_text = " ".join(text_lines)

    logger.debug("Parsed: emotion=%s actions=%d text=%d chars",
                 emotion, len(actions), len(final_text))

    return ParsedResponse(emotion=emotion, actions=actions, text=final_text)
