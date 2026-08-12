"""
memory/manager.py — Per-user long-term memory backed by ChromaDB.

KEY FIX: Each user gets their own ChromaDB collection (user_{uid}).
This prevents memory bleeding between users in groups.
"""
import datetime
import logging
import chromadb
from config import settings
from utils.datetime_utils import get_datetime_in_words

logger = logging.getLogger("amy.memory")

SAVE_TRIGGERS = frozenset([
    "remember this", "don't forget", "note this",
    "memorize this", "keep this in mind",
    "یادت باشه", "فراموش نکن", "حفظ کن", "به خاطر بسپار",
])
AI_TRIGGERS = frozenset([
    "i will remember", "i'll keep in mind",
    "i won't forget", "i will never forget",
    "یادم میمونه", "فراموش نمیکنم",
])
EMOTIONAL_TRIGGERS = frozenset([
    "scared", "sad", "lonely", "afraid", "worried",
    "hurt", "cry", "panic", "depressed",
    "ترسیدم", "غمگین", "تنها", "گریه", "افسرده",
])
RECALL_TRIGGERS = [
    "recall", "remember when", "what did i say",
    "do you remember", "i told you", "previously",
    "یادته", "یادت میاد", "قبلاً گفتم", "یادت هست",
]


class MemoryManager:
    def __init__(self):
        self._client     = chromadb.PersistentClient(path=settings.chroma_path)
        self._cols: dict[int, any] = {}      # uid → collection
        self._last_save: dict[int, datetime.datetime] = {}

    def _get_collection(self, user_id: int):
        """Each user has their own isolated collection: 'user_<uid>'."""
        if user_id not in self._cols:
            col_name = f"user_{user_id}"
            try:
                self._cols[user_id] = self._client.get_or_create_collection(
                    name=col_name
                )
                logger.debug("Opened memory collection '%s'", col_name)
            except Exception as e:
                logger.error("Failed to open collection for user %d: %s", user_id, e)
                return None
        return self._cols[user_id]

    # ── Retrieval ─────────────────────────────────────────

    def retrieve_relevant(self, user_id: int, user_input: str, n: int = 2) -> str:
        """Returns semantically relevant memories if recall is triggered."""
        if not any(t in user_input.lower() for t in RECALL_TRIGGERS):
            return ""
        col = self._get_collection(user_id)
        if not col:
            return ""
        try:
            results = col.query(query_texts=[user_input], n_results=n)
            docs    = results.get("documents", [[]])[0]
            return "\n".join(docs) if docs else ""
        except Exception as e:
            logger.error("Memory retrieval error uid=%d: %s", user_id, e)
            return ""

    def retrieve_latest(self, user_id: int, n: int = 3) -> str:
        """Returns n most recent memories for this user (used on session start)."""
        col = self._get_collection(user_id)
        if not col:
            return ""
        try:
            results = col.get(include=["documents", "metadatas"])
            if not results["documents"]:
                return ""
            pairs = sorted(
                zip(results["metadatas"], results["documents"]),
                key=lambda x: x[0].get("timestamp_iso", ""),
                reverse=True,
            )
            return "\n".join(doc for _, doc in pairs[:n])
        except Exception as e:
            logger.error("Latest memory error uid=%d: %s", user_id, e)
            return ""

    # ── Saving ────────────────────────────────────────────

    def maybe_save(self, user_id: int, user_in: str, ai_out: str) -> None:
        """Saves memory only when triggered and cooldown has passed."""
        now  = datetime.datetime.now()
        last = self._last_save.get(user_id)
        if last and (now - last).total_seconds() < settings.memory_cooldown_seconds:
            return

        lower_in  = user_in.lower()
        lower_out = ai_out.lower()

        should_save = (
            any(t in lower_in  for t in SAVE_TRIGGERS)
            or any(t in lower_out for t in AI_TRIGGERS)
            or any(e in lower_in  for e in EMOTIONAL_TRIGGERS)
        )
        if not should_save:
            return

        col = self._get_collection(user_id)
        if not col:
            return

        try:
            count   = col.count()
            mem_id  = f"mem_{count}_{user_id}_{int(now.timestamp())}"
            content = (
                f"[Memory | {get_datetime_in_words()}]\n"
                f"User said: {user_in}\n"
                f"Amy replied: {ai_out}"
            )
            col.add(
                documents=[content],
                ids=[mem_id],
                metadatas=[{
                    "timestamp_iso": now.isoformat(),
                    "user_id":       str(user_id),
                    "type":          "episodic",
                }],
            )
            self._last_save[user_id] = now
            logger.info("Memory saved for user %d (total: %d)", user_id, count + 1)
        except Exception as e:
            logger.error("Memory save error uid=%d: %s", user_id, e)
