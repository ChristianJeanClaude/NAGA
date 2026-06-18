# NAGA Scout Bot

## Overview

NAGA Scout Bot is a Discord bot that helps a games-publishing team scout
prospective titles on Steam. When team members react to a Steam link posted in
a dedicated channel, the bot fetches the game's data from Steam, SteamSpy, and
the Steam store page, computes a relevance score, and creates a structured
entry in a Notion database.

The bot is designed to operate silently: it logs to stdout and does not post
confirmation messages to Discord.

## Features

- **Auto-reactions on Steam links** — posts a set of voting reactions on any
  message containing a Steam store link.
- **Duplicate detection** — checks Notion (and a local SQLite cache) so the
  same game is never scouted twice.
- **Automatic Notion entry creation** — aggregates metadata from Steam,
  SteamSpy, and the store page into a new Notion page.
- **Relevance score** — a 0–100 score derived from review sentiment, owner
  estimates, peak concurrent players, and review volume.

## Project Structure

```
naga-scout-bot/
├── main.py                     # Entry point: configures logging, inits the cache, runs the bot
├── config.py                   # Loads and validates environment variables (fails fast if missing)
├── railway.toml                # Railway deployment configuration
├── nixpacks.toml               # Nixpacks build configuration (pins Python 3.12)
├── pytest.ini                  # Pytest configuration (asyncio mode, warning filters)
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Development/test dependencies
├── .env.example                # Template for required environment variables
├── bot/
│   ├── __init__.py
│   └── events.py               # Discord events (on_message, on_raw_reaction_add)
├── services/
│   ├── __init__.py
│   ├── steam.py                # Steam appdetails API, store-page scraping, SteamSpy, App ID extraction
│   ├── notion.py               # Notion reads/writes: find/create pages, get page ID, list all App IDs
│   ├── notion_update.py        # Updates an existing Notion page
│   ├── cache.py                # Local SQLite cache for message deduplication
│   ├── retry.py                # Centralized async retry with exponential backoff
│   ├── scoring.py              # Relevance-score computation
│   └── suggest.py              # NAGA profile + Steam suggestion search
├── models/
│   ├── __init__.py
│   └── game.py                 # GameData dataclass + Notion property serialization
└── tests/
    ├── __init__.py
    ├── conftest.py             # Shared test fixtures
    ├── test_steam.py           # Tests for App ID extraction
    ├── test_game.py            # Tests for Steam date parsing
    ├── test_cache.py           # Tests for the SQLite dedup cache
    └── test_scoring.py         # Tests for the relevance score
```

## Setup

### Prerequisites

- **Python 3.12+**
- A **Discord server** where you have administrator permissions
- A **Notion workspace** with a database to receive scouted games
- A **Steam account** (to create a Steam Web API key)

### Installation

1. **Clone the repository**

   ```bash
   git clone <your-repo-url>
   cd naga-scout-bot
   ```

2. **Create and activate a virtual environment**

   ```bash
   python -m venv .venv
   # Windows (PowerShell)
   .venv\Scripts\Activate.ps1
   # macOS / Linux
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   # For running tests:
   pip install -r requirements-dev.txt
   ```

4. **Configure environment variables**

   ```bash
   cp .env.example .env
   ```

   Then open `.env` and fill in each value (see below).

### Environment Variables

| Variable | Description | Where to find it |
|---|---|---|
| `DISCORD_TOKEN` | Bot authentication token. | [Discord Developer Portal](https://discord.com/developers/applications) → your application → **Bot** → **Reset Token**. |
| `DISCORD_CHANNEL_ID` | ID of the channel where Steam links are posted and scouted. | Enable **Developer Mode** in Discord (Settings → Advanced), then right-click the channel → **Copy Channel ID**. |
| `DISCORD_SUGGEST_CHANNEL_ID` | ID of the channel reserved for game suggestions. | Right-click the suggestions channel → **Copy Channel ID**. |
| `NOTION_TOKEN` | Internal integration secret used to read/write the database. | [Notion Integrations](https://www.notion.so/my-integrations) → **New integration** → copy the **Internal Integration Secret**. Share the database with the integration. |
| `NOTION_DATABASE_ID` | ID of the target Notion database. | Open the database as a full page; the 32-character ID is in the URL: `notion.so/<workspace>/<DATABASE_ID>?v=...`. |
| `STEAM_API_KEY` | Steam Web API key. | [Steam Web API Key registration](https://steamcommunity.com/dev/apikey). |

> **Note:** All six variables are required at startup — the bot fails fast
> with a clear error if any is missing. `STEAM_API_KEY` is validated on startup
> and reserved for future use; the current data sources (appdetails, store
> search, SteamSpy) do not require it.

### Notion Database Setup

The target database must contain the following columns. Names and types must
match exactly, or the Notion API will reject writes.

| Column | Notion Type |
|---|---|
| Game | Title |
| Steam App ID | Number |
| Steam URL | URL |
| Description | Text |
| Developer | Text |
| Genres | Multi-select |
| Tags | Multi-select |
| Website | URL |
| Review Score | Select |
| Review Count | Number |
| Owners Estimate | Text |
| Peak CCU | Number |
| Twitter URL | URL |
| Discord URL | URL |
| Scouted By | Text |
| Relevance Score | Number |
| Status | Select |

> `Status` is set automatically to `Scouted` on creation. The bot writes the
> 16 data columns above plus `Status` (17 total).

### Discord Bot Setup

In the [Discord Developer Portal](https://discord.com/developers/applications),
under your application's **Bot** tab, enable the following **Privileged Gateway
Intents**:

- **Message Content Intent** — required to read message text and detect Steam
  links.
- **Server Members Intent** — required to resolve reacting members.

The bot requests the default intents plus **message content**, **reactions**,
and **members**.

When inviting the bot (OAuth2 → URL Generator), grant it the `bot` scope and at
least the following permissions in the scouting channel:

- View Channels
- Send Messages
- Embed Links
- Add Reactions
- Read Message History

## Usage

### Automatic scouting

1. A team member posts a Steam store link in the scouting channel.
2. The bot checks Notion: if the game already exists, it posts a short
   "already scouted" notice and stops. Otherwise it adds two voting reactions
   (👍 👎).
3. Once **two distinct (non-bot) members** have reacted, the bot fetches the
   game's data from Steam, the store page, and SteamSpy, computes the relevance
   score, and creates a Notion entry. The bot stays silent — no confirmation
   message is posted; progress is logged to the console only.

## Bot Flow Diagram

```
Steam link posted in the scouting channel
                │
                ▼
on_message: extract App ID
Already in Notion?
        │               │
       yes              no
        │               │
        ▼               ▼
Post duplicate      Add 2 reactions
notice; stop        👍 👎
                        │
                        ▼
            ≥ 2 distinct human reactions
                        │
                        ▼
            on_raw_reaction_add
            • dedup (SQLite cache)
            • dedup guard (Notion)
                        │
                        ▼
            fetch_game_data:
              Steam appdetails + store scrape + SteamSpy
            compute_relevance_score
                        │
                        ▼
            create Notion page
            mark message as processed
            (silent — console logging only)
```

## Deployment (Railway)

The repository includes `railway.toml` and `nixpacks.toml` for deployment on
[Railway](https://railway.app/).

1. Create a new Railway project and connect this repository.
2. Railway detects `nixpacks.toml` and builds with Python 3.12, installing
   `requirements.txt`.
3. Add every environment variable from the table above under the service's
   **Variables** tab.
4. Deploy. The service runs `python main.py` (a long-running worker; the health
   check is disabled because the bot does not expose an HTTP port).
5. The restart policy retries on failure up to five times.

> **Persistence note:** the local SQLite dedup cache lives in `db/scouting.db`, which is
> ephemeral on Railway and resets on each redeploy. Notion-side duplicate
> detection still prevents duplicate entries; only the local fast-path cache is
> reset.

## Development

### Running tests

```bash
pip install -r requirements-dev.txt
pip install -r requirements.txt
pytest
```

Tests are offline by design: there are no network calls. Async tests run under
`pytest-asyncio` in auto mode (configured in `pytest.ini`).

### Project conventions

- **All I/O is async** — Discord, HTTP (`aiohttp`), Notion, and SQLite
  (`aiosqlite`) calls are awaited; blocking calls are avoided.
- **Retry with exponential backoff** — external calls go through
  `services/retry.py` (`with_retry`), which retries on transient errors with
  delays of 2s, 4s, 8s.
- **Silent operation** — the bot logs to stdout and does not post confirmation
  messages to Discord; it only adds voting reactions. Failures in non-critical
  paths are logged and swallowed so they never break the pipeline.
```
