"""Configuration et chargement des variables d'environnement.

Charge les variables depuis un fichier `.env` (via python-dotenv) et les
expose comme constantes de module. Lève une `ValueError` explicite au
démarrage si une variable requise est absente, afin d'échouer tôt plutôt
que de planter plus tard avec une erreur obscure.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path("db/scouting.db")


def _require(name: str) -> str:
    """Retourne la variable d'environnement `name` ou lève une ValueError."""
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing environment variable: {name}")
    return value


DISCORD_TOKEN: str = _require("DISCORD_TOKEN")

_channel_id_raw = _require("DISCORD_CHANNEL_ID")
try:
    DISCORD_CHANNEL_ID: int = int(_channel_id_raw)
except ValueError:
    raise ValueError(
        f"DISCORD_CHANNEL_ID must be a valid integer, got: {_channel_id_raw}"
    )

_scout_log_raw = _require("DISCORD_SCOUT_LOG_CHANNEL_ID")
try:
    DISCORD_SCOUT_LOG_CHANNEL_ID = int(_scout_log_raw)
except ValueError:
    raise ValueError(
        f"DISCORD_SCOUT_LOG_CHANNEL_ID must be a valid integer, got: {_scout_log_raw}"
    )

_suggest_channel_raw = _require("DISCORD_SUGGEST_CHANNEL_ID")
try:
    DISCORD_SUGGEST_CHANNEL_ID = int(_suggest_channel_raw)
except ValueError:
    raise ValueError(
        f"DISCORD_SUGGEST_CHANNEL_ID must be a valid integer, got: {_suggest_channel_raw}"
    )

_cmd_channel_raw = _require("DISCORD_CMD_CHANNEL_ID")
try:
    DISCORD_CMD_CHANNEL_ID = int(_cmd_channel_raw)
except ValueError:
    raise ValueError(
        f"DISCORD_CMD_CHANNEL_ID must be a valid integer, got: {_cmd_channel_raw}"
    )

NOTION_TOKEN: str = _require("NOTION_TOKEN")
NOTION_DATABASE_ID: str = _require("NOTION_DATABASE_ID")
STEAM_API_KEY: str = _require("STEAM_API_KEY")

# Identifiants Bluesky optionnels : os.environ.get (et non _require) car le
# scouting Bluesky est facultatif et le bot doit démarrer sans ces variables.
BLUESKY_HANDLE: str = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_PASSWORD: str = os.environ.get("BLUESKY_PASSWORD", "")
