# Amy v5 — Modular Tsundere AI Bot

A Telegram bot with personality, per-chat shared memory, multi-tool support,
media (photo/sticker/GIF/video) understanding, and passive group listening.

## Architecture

```
amy/
├── main.py              ← Entry point
├── config.py             ← All settings from env vars
├── requirements.txt
├── railway.json          ← Railway deployment config
├── .env.example
│
├── core/
│   ├── brain.py          ← Central orchestrator (User → Brain → Tool → LLM → Telegram)
│   ├── parser.py         ← LLM output parser (emotion, action, text)
│   ├── conversation.py   ← Chat-scoped message history (shared in groups, tagged by sender)
│   ├── emotion_engine.py ← Tracks Amy's emotional state per chat
│   └── language.py       ← Language detection (fa / en)
│
├── llm/
│   └── groq_client.py    ← Groq API wrapper (chat / fast_chat / vision)
│
├── memory/
│   └── manager.py        ← ChromaDB long-term memory, per person (save + retrieve)
│
├── tools/
│   ├── web_search.py     ← SerpAPI / DuckDuckGo search
│   ├── image_search.py   ← Pixabay photo search
│   └── image_generate.py ← Pollinations.ai image generation
│
├── handlers/
│   └── bot.py             ← Telegram handlers, passive-listening logic, result dispatcher
│
├── personalities/
│   └── amy.py             ← System prompt + few-shot examples
│
├── utils/
│   └── media.py            ← Sticker/GIF/video → single JPEG frame (for vision)
│
└── models/
    └── schemas.py          ← Shared data models (Emotion, Action, ParsedResponse, UserContext, ...)
```

## Flow

```
Telegram update
    ↓
Addressed to Amy (mention/reply)?
    ├── yes → Brain.process() / process_image() → LLM → parse_response() → deliver (quoted reply)
    │
    └── no (group only) → passive-listening gate:
            cheap checks (length/cooldown/random) → Brain.should_engage() [fast model]
                ├── no  → Brain.observe() — stored in shared history, no reply
                └── yes → Brain.process(spontaneous=True) → deliver (plain message, no quote)
```

History is keyed by **chat_id**, not user_id — in a group, every member sees
and is seen in ONE shared conversation (tagged `Name: message`), which is
what lets Amy connect something said by one person to something a different
person does later. Long-term memory (ChromaDB) stays per-person.

## Setup

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in your keys in .env
```

Required:
- `TELEGRAM_TOKEN` — from @BotFather
- `GROQ_API_KEY` — free, from console.groq.com/keys
  (or `GROQ_API_KEYS=key1,key2,key3` — comma-separated, for multiple free
  accounts. When one key hits its rate limit, calls rotate to the next key
  for the *same* model — the model never changes on its own, so personality
  and reply quality stay identical regardless of which key is active)

Optional:
- `GROQ_CHAT_MODEL` (default `openai/gpt-oss-120b`) — main personality model
- `GROQ_FAST_MODEL` (default `openai/gpt-oss-20b`) — cheap model used only for
  the passive "should I jump in?" gate, on its own separate free-tier quota
- `GROQ_VISION_MODEL` (default `qwen/qwen3.6-27b`) — the only Groq model with
  vision support right now
- `PASSIVE_LISTENING_ENABLED` (default `true`) — set `false` to make Amy
  fully reactive again (only mention/reply triggers her)
- `PASSIVE_PRECHECK_PROBABILITY` (default `0.25`) — chance an eligible
  message even gets sent to the fast-model gate
- `PASSIVE_COOLDOWN_SECONDS` (default `45`) — minimum gap between two
  spontaneous replies in the same chat
- `MODERATION_ENABLED` (default `true`) — Amy can mute/ban whoever sent a
  genuinely abusive message directed at her. The target is always locked to
  the real sender of the message being processed — never something the model
  can redirect. An outright ban only actually happens after
  `MODERATION_BAN_AFTER_STRIKES` offenses (default `4`); before that, a
  requested ban is downgraded to an escalating mute
  (`MODERATION_MUTE_MINUTES`, default `5,30,180`). Strikes decay after
  `MODERATION_STRIKE_RESET_HOURS` (default `72`). Group admins/the creator
  are never touched. Every action is posted in the group, never silent.
- `MUTE_DEFAULT_MINUTES` / `MUTE_MAX_MINUTES` (default `10` / `180`) — clamp
  for a single mute Amy requests directly (not a downgraded ban)
- `SERPAPI_KEY` — better web search (falls back to DuckDuckGo if missing)
- `PIXABAY_API_KEY` — real photo search (send_image action)

Groq retires model IDs on fairly short notice — if requests start failing,
check https://console.groq.com/docs/deprecations for the current
recommended replacement and set the matching env var above.

### 3. Give the bot group admin rights (required for both features below)

**Passive listening** — in @BotFather: your bot → **Bot Settings → Group
Privacy → Turn off**. Without this, Telegram never forwards messages that
don't @mention or reply to Amy, so she can only ever be reactive in groups.

**Moderation (mute/ban)** — in the actual Telegram group: promote the bot to
**admin** and grant it **"Restrict members"** and **"Ban users"**. Without
this, moderation calls just fail gracefully (Amy posts a note saying she's
not an admin yet, instead of silently doing nothing).

### 4. Run

```bash
python main.py
```

### Deploy to Railway

1. Push to GitHub
2. Connect repo on railway.app
3. Set env vars in Railway dashboard
4. Deploy — `railway.json` handles the rest

## Commands

| Command  | Description                                    |
|----------|-------------------------------------------------|
| /start   | Wake Amy up                                      |
| /reset   | Clear this chat's conversation history (shared group history included) |

## Adding New Tools

1. Create `tools/your_tool.py` with an async function
2. Add a new `ActionType` in `models/schemas.py`
3. Handle the new type in `core/brain.py` → `_execute_single_action()`
4. Add it to the system prompt in `personalities/amy.py`
