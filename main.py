"""Point d'entrée du bot Discord naga-scout-bot.

Configure la journalisation, initialise la base SQLite de cache, puis démarre
le bot Discord et le scheduler des jobs récurrents dans la même boucle asyncio.

``bot.start()`` (et non ``bot.run()``) est utilisé pour rester dans un contexte
``async`` : le bot Discord et ``start_scheduler()`` tournent côte à côte dans un
``asyncio.TaskGroup``.
"""

import asyncio
import logging

from services.cache import init_db
from scheduler import start_scheduler
from bot.events import bot
from config import DISCORD_TOKEN

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    await init_db()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(start_scheduler())
        tg.create_task(bot.start(DISCORD_TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
