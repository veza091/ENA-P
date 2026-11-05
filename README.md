# ENA-P

**A Discord companion bot with long-term memory, an evolving mood model, and quota-aware Gemini routing.**

ENA-P is an open-source Discord bot written in Python. Instead of answering every message in isolation, it keeps a persistent, human-like memory of each user: facts that fade if they are never mentioned again, shared episodes with an emotional tone, and upcoming events it can follow up on. Replies are generated with Google's Gemini API, and the bot is designed to survive free-tier quota limits by rotating models and API keys.

The whole bot currently lives in a single file, `ena.py`.

> **Status:** early-stage, actively developed by a solo maintainer. It currently runs on a single Discord community. Expect rough edges, and feedback is welcome.

---

## Features

### Memory
- **Fact memory with decay.** Facts about each user are stored with a confidence and a strength score. Strength fades with time since last recall and is reinforced whenever a fact is used again.
- **Change and contradiction tracking.** When a user's stated fact changes, the previous values are kept so the bot can notice the change instead of silently overwriting it.
- **Episodic memory.** Conversations are condensed into short episodes with topics, an importance score (1–10) and an emotional tag. The most recent episode plus the most relevant older ones are recalled.
- **Anticipations.** Future events mentioned by a user (with an optional due date) are stored and surfaced when they become relevant.
- **Associative retrieval.** Memories are selected by keyword overlap with the current message, using Arabic-aware text normalization (letter unification, diacritics removal, article-prefix stripping).
- **Self-wipe.** A user can ask the bot to forget everything about them with the trigger phrase `امسح ذاكرتي`.
- **Automatic housekeeping.** On startup the bot deletes raw messages older than **90 days**, long-unrecalled weak memories (**90 days**), and stale anticipations. Episodes are never deleted.

### Mood
- A **circumplex mood model** (valence × arousal) per user, updated with an exponential moving average and decaying back toward a calm baseline over time.
- Mood is mapped to six named zones and transitions between zones are logged.

### Replies
- **Image understanding.** Up to three attached images (PNG / JPEG / WebP, ≤ 7 MB each) are sent to the model, so the bot can react to pictures and memes.
- **Time awareness.** Replies can reference the current time of day and how long ago something happened.
- **Proactive messages.** The bot can start conversations on its own, with a daily cap (6), a per-user gap (8 h), quiet hours (01:00–08:00) and activity checks. Enabled by default (`PROACTIVE_ENABLED=1`).
- **Shadow mode.** `PROACTIVE_SHADOW` (off by default) computes and logs proactive opportunities without sending anything — useful for measuring readiness before turning the feature loose.

### Reliability
- **Model rotation.** Several Gemini models are tried in order; separate model lists are used for replies, memory extraction and episode summarization (see table below).
- **Quota awareness.** When all models for a key return `429`, the key is marked exhausted until the free-tier quota resets (midnight Pacific time). Multiple memory API keys can be supplied and are rotated automatically.
- **Background Gemma extraction.** Memory extraction can run through a background Gemma model (`gemma-4-31b-it` by default) on an independent quota, falling back to Gemini if it times out or fails.
- **Group awareness.** Optionally archives messages from selected channels (database only, no LLM calls) so the bot understands channel activity.

### Optional features (off by default)
- **Semantic retrieval.** Embedding-based search with `gemini-embedding-001` (768 dimensions). Disabled by default (`EMBED_ENABLED=0`).
- **Affect layer.** An emotional scoring pass that runs entirely in the background after the reply is sent. Disabled by default (`AFFECT_ENABLED=0`).
- **Live voice.** Full voice-channel conversations driven by `gemini-3.8-live`. Gated behind several switches (`LIVE_VOICE_ENABLED`, a named `VOICE_CHANNEL`, and the `davey` + `PyNaCl` libraries) and capped at 3 spontaneous joins per day.

### Database safety
- The PostgreSQL schema is created and upgraded automatically on startup.
- Row Level Security is enabled on every table, and access is revoked from the `anon` and `authenticated` roles.
- All queries are parameterized. Related writes run inside transactions.
- Credentials are read from environment variables only.

---

## Models

| Role | Models (tried in order) |
|---|---|
| Replies | `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3.5-flash`, `gemini-3.7-flash` |
| Memory extraction | `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite` |
| Episode summaries | `gemini-3.5-flash`, `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite` |
| Background memory | `gemma-4-31b-it` (independent quota, optional fallback) |
| Embeddings | `gemini-embedding-001` (768 dims, off by default) |
| Live voice | `gemini-3.8-live` |

---

## Architecture

```
Discord  <-->  discord.py client  -->  Gemini API (reply / memory / episode models)
                     |
                     v
          PostgreSQL (Supabase-compatible)
   users · messages · memories · episodes · mood_log
   anticipations · proactive_log
```

A diagram is included at [`docs/ENA-Architecture-v2.drawio`](docs/ENA-Architecture-v2.drawio).

---

## Requirements

- Python 3.9 or newer
- A Discord bot token with the **Message Content** intent enabled
- A Google Gemini API key
- A PostgreSQL database (a free Supabase project works)

## Installation

```bash
git clone https://github.com/veza091/ENA-P.git
cd ENA-P
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

Create a `.env` file next to `ena.py`. **Never commit this file.**

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | yes | Discord bot token |
| `MAIN_GEMINI_API_KEY` | yes | Gemini key used for replies (`GEMINI_API_KEY` also works) |
| `DATABASE_URL` | yes | PostgreSQL connection string |
| `MEMORY_GEMINI_API_KEY` | no | Separate key for memory work (defaults to the main key) |
| `MEMORY_GEMINI_API_KEYS` | no | Comma-separated list of keys, rotated on quota exhaustion |
| `DATABASE_SSL` | no | `1`/`true` to force SSL (enabled automatically for Supabase URLs) |
| `PORT` | no | Port for the health-check web server (default `8080`) |
| `WATCH_CHANNELS` | no | Comma-separated channel IDs to archive. Empty disables the feature |
| `PROACTIVE_ENABLED` | no | `1` to allow proactive messages, `0` to disable (default `1`) |
| `PROACTIVE_SHADOW` | no | `1` to log proactive opportunities without sending (default `0`) |
| `GEMMA_BACKGROUND_ENABLED` | no | `0` to disable the background Gemma extraction (default `1`) |
| `EMBED_ENABLED` | no | `1` to enable semantic embedding retrieval (default `0`) |
| `AFFECT_ENABLED` | no | `1` to enable the background affect layer (default `0`) |
| `LIVE_VOICE_ENABLED` | no | `1` to enable live voice (still gated by `VOICE_CHANNEL`, default `1`) |
| `VOICE_CHANNEL` | no | Voice channel ID to join for live voice |

Example:

```env
DISCORD_TOKEN=your-discord-token
MAIN_GEMINI_API_KEY=your-gemini-key
DATABASE_URL=postgresql://user:password@host:5432/postgres
PROACTIVE_ENABLED=0
```

## Running

```bash
python ena.py
```

On first start the bot creates all required tables. A small web server exposes `/` and `/health` so the bot can be kept alive on free hosting platforms.

## Running the checks

The repository ships a dependency-free self-test that extracts the bot's pure functions with `ast` and checks them without touching Discord or the database.

```bash
pip install -r requirements.txt   # PyNaCl is required by the self-test
python tests/ena_selftest.py
```

All **97 checks** pass against `ena.py` (last verified on 2026-11-05). The self-test exits non-zero if any check fails.

---

## Privacy and data

ENA-P stores user messages and derived memories in your own database. By default:

- raw messages are deleted after **90 days**;
- weak, long-unrecalled memories are deleted after **90 days**;
- stored data is limited to Discord user IDs, display names, message content and the derived memories, episodes and mood values.

If you run the bot in a community, tell its members that the bot remembers what they say. Messages sent to the Gemini API are subject to Google's own terms and privacy policy.

## Current limitations

- Single-file codebase (`ena.py`, ~3.9k lines); splitting it into modules is planned.
- The bot's persona and prompts are written for Gulf-dialect Arabic.
- The included self-test covers pure functions (97 checks); full integration tests are not included yet.
- Tested on a single Discord server.

## Contributing

Issues and pull requests are welcome, especially for tests, documentation and splitting the code into modules. Please open an issue first for larger changes.

## License

Released under the [MIT License](LICENSE).
