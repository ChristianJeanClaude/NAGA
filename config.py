"""Configuration et chargement des variables d'environnement.

Charge les variables depuis un fichier `.env` (via python-dotenv) et les
expose comme constantes de module. Lève une `ValueError` explicite au
démarrage si une variable requise est absente, afin d'échouer tôt plutôt
que de planter plus tard avec une erreur obscure.
"""

import os

from dotenv import load_dotenv

load_dotenv()


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

NOTION_TOKEN: str = _require("NOTION_TOKEN")
NOTION_DATABASE_ID: str = _require("NOTION_DATABASE_ID")
STEAM_API_KEY: str = _require("STEAM_API_KEY")
