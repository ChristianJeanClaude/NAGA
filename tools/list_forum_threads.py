"""Diagnostic : liste les threads du forum cible (id, titre, nb de messages).

Utile pour vérifier ce que le bot va scraper et pour les opérations de
réconciliation Notion. Lecture seule, se connecte puis se déconnecte.

Usage (depuis la racine) :
    python tools/list_forum_threads.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bots"))

import discord  # noqa: E402
import discord_bot as bot  # noqa: E402


class Lister(discord.Client):
    def __init__(self, channel_id, **kwargs):
        super().__init__(**kwargs)
        self._channel_id = channel_id
        self.threads_info = []

    async def on_ready(self):
        channel = self.get_channel(self._channel_id) or \
            await self.fetch_channel(self._channel_id)
        if isinstance(channel, discord.ForumChannel):
            for thread in await bot.all_forum_threads(channel):
                count = 0
                async for _ in thread.history(limit=None):
                    count += 1
                self.threads_info.append((str(thread.id), thread.name, count))
        await self.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = bot.load_env_file(bot.ENV_DISCORD, ["DISCORD_TOKEN", "DISCORD_CHANNEL_ID"])
    intents = discord.Intents.default()
    intents.message_content = True
    client = Lister(int(env["DISCORD_CHANNEL_ID"]), intents=intents)
    client.run(env["DISCORD_TOKEN"], log_handler=None)

    print(f"{len(client.threads_info)} thread(s) :")
    for thread_id, name, count in client.threads_info:
        print(f"  {thread_id}  {count:>3} msg  {name!r}")


if __name__ == "__main__":
    main()
