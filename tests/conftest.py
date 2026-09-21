"""Fixtures partagées pour la suite de tests de naga-scout-bot.

Fournit des variables d'environnement factices pour que ``config.py``
s'importe sans ``.env`` (les tests ne touchent jamais au réseau). Les fixtures
spécifiques (ex. base SQLite temporaire) sont définies dans les modules de
test concernés.
"""

import os

for _name, _value in {
    "DISCORD_TOKEN": "test-token",
    "DISCORD_CHANNEL_ID": "1000",
    "DISCORD_SUGGEST_CHANNEL_ID": "2000",
    "NOTION_TOKEN": "test-token",
    "NOTION_DATABASE_ID": "test-db",
    "STEAM_API_KEY": "test-key",
}.items():
    os.environ.setdefault(_name, _value)
