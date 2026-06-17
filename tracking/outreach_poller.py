"""Polling Notion pour détecter les jeux outreachés et ajouter ✅ sur Discord."""

import logging
import os
from datetime import datetime, timezone, timedelta

from notion.reader import NotionReader
from bot.events import bot

logger = logging.getLogger(__name__)

# Fichier local pour stocker le dernier check
LAST_CHECK_FILE = "db/last_outreach_check.txt"


async def _get_last_check() -> datetime:
    """
    Lit l'horodatage du dernier check depuis db/last_outreach_check.txt.
    Retourne un datetime d'il y a 1 heure si le fichier n'existe pas (ou est
    illisible).
    """
    fallback = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        with open(LAST_CHECK_FILE, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
        if not raw:
            return fallback
        return datetime.fromisoformat(raw)
    except (FileNotFoundError, ValueError):
        return fallback


async def _save_last_check(dt: datetime) -> None:
    """
    Sauvegarde l'horodatage courant dans db/last_outreach_check.txt.
    """
    os.makedirs(os.path.dirname(LAST_CHECK_FILE) or ".", exist_ok=True)
    with open(LAST_CHECK_FILE, "w", encoding="utf-8") as handle:
        handle.write(dt.isoformat())


async def run_outreach_poll() -> None:
    """
    Fonction principale de polling :
    1. Récupère l'horodatage du dernier check
    2. Interroge Notion pour les jeux outreachés depuis ce check
    3. Pour chaque jeu ayant une Discord Message URL :
       a. Récupère le message Discord
       b. Ajoute la réaction ✅ si elle n'est pas déjà présente
    4. Sauvegarde l'horodatage courant comme dernier check
    Ne raise jamais.
    """
    try:
        now = datetime.now(timezone.utc)
        since = await _get_last_check()

        reader = NotionReader()
        pages = await reader.get_newly_outreached(since)

        logger.info(
            f"Outreach poller: {len(pages)} page(s) outreachée(s) depuis {since}"
        )

        for page in pages:
            discord_url = page.get("discord_message_url")
            if not discord_url:
                continue

            try:
                # Extrait message_id et channel_id depuis l'URL
                # Format : https://discord.com/channels/{guild_id}/{channel_id}/{message_id}
                parts = discord_url.rstrip("/").split("/")
                message_id = int(parts[-1])
                channel_id = int(parts[-2])

                channel = bot.get_channel(channel_id)
                if channel is None:
                    channel = await bot.fetch_channel(channel_id)

                message = await channel.fetch_message(message_id)

                # Vérifie si ✅ est déjà présent
                already_reacted = any(
                    str(r.emoji) == "✅" and r.me
                    for r in message.reactions
                )

                if not already_reacted:
                    await message.add_reaction("✅")
                    logger.info(f"✅ ajouté sur message {message_id}")

            except Exception as exc:
                logger.warning(f"Impossible d'ajouter ✅ sur {discord_url}: {exc}")

        await _save_last_check(now)

    except Exception as exc:
        logger.error(f"Outreach poller — ÉCHEC: {exc}", exc_info=True)
