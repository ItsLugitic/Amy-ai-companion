# Amy v3 — Modular Tsundere AI Bot

A professional, modular Telegram bot with personality, memory, and multi-tool support.

## Architecture

```
amy/
├── main.py              ← Entry point
├── config.py            ← All settings from env vars
├── requirements.txt
├── railway.json         ← Railway deployment config
├── .env.example
│
├── core/
│   ├── brain.py         ← Central orchestrator (User → Brain → Tool → LLM → Telegram)
│   ├── parser.py        ← LLM output parser (emotion, action, text)
│   ├── conversation.py  ← Per-user message history
│   ├── emotion_engine.py← Tracks Amy's emotional state per user
│   └── language.py      ← Language detection (fa / en)
│
├── llm/
│   └── groq_client.py   ← Groq API wrapper with model fallback
│
├── memory/
│   └── manager.py       ← ChromaDB long-term memory (save + retrieve)
│
├── tools/
│   ├── web_search.py    ← SerpAPI / DuckDuckGo search
│   ├── image_search.py  ← Pixabay photo search
│   └── image_generate.py← Pollinations.ai image generation
│
├── telegram/
│   └── bot.py           ← Telegram handlers + result dispatcher
│
├── personalities/
│   └── amy.py           ← System prompt + few-shot examples
│
└── models/
    └── schemas.py        ← Shared data models (Emotion, Action, ParsedResponse, ...)
```

## Flow

```
User Message
    ↓
Brain.process()
    ↓
Memory.retrieve_relevant()     ← ChromaDB semantic search
    ↓
Build contextual message
    ↓
LLM call (Groq with fallback)
    ↓
parse_response()               ← Extract emotion, action, text
    ↓
Execute tool? (web_search / send_image / generate_image)
    ↓
BrainResult → Telegram handler → User
```

## Setup

### 1. Clone and install

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
- `GROQ_API_KEY` — from console.groq.com

Optional:
- `SERPAPI_KEY` — better web search (falls back to DuckDuckGo if missing)
- `PIXABAY_API_KEY` — real photo search (send_image action)

### 3. Run

```bash
python main.py
```

### Deploy to Railway

1. Push to GitHub
2. Connect repo on railway.app
3. Set env vars in Railway dashboard
4. Deploy — `railway.json` handles the rest

## LLM Models (fallback order)

1. `gemma2-9b-it` — highest free TPM
2. `llama-3.1-8b-instant` — fast and lightweight
3. `llama3-8b-8192` — third fallback
4. `llama-3.3-70b-versatile` — highest quality, last resort

## Commands

| Command  | Description                  |
|----------|------------------------------|
| /start   | Wake Amy up                  |
| /reset   | Clear conversation history   |

## Adding New Tools

1. Create `tools/your_tool.py` with an async function
2. Add a new `ActionType` in `models/schemas.py`
3. Handle the new type in `core/brain.py` → `_execute_action()`
4. Add it to the system prompt in `personalities/amy.py`
