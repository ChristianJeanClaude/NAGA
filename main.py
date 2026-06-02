"""Point d'entrée du bot Discord naga-scout-bot.

Configure la journalisation, initialise la base SQLite de cache, puis démarre
le bot Discord. ``init_db()`` est exécuté dans une boucle asyncio dédiée avant
de lancer ``bot.run()`` (qui gère sa propre boucle d'événements).
"""

import asyncio
import logging

from services.cache import init_db
from bot.events import bot
from config import DISCORD_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    await init_db()


if __name__ == "__main__":
    asyncio.run(main())
    bot.run(DISCORD_TOKEN)
