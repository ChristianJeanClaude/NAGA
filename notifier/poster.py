"""Publication des résultats de scouting/tracking dans Discord.

Poste un digest des candidats détectés par le ScoutingJob directement via le
canal du bot Discord (``DISCORD_CHANNEL_ID``), en réutilisant l'instance ``bot``
de ``bot.events``. Aucune méthode ne propage d'exception : un échec de
notification ne doit jamais faire échouer le job appelant.
"""

import logging
import os

import discord

from bot.events import bot

logger = logging.getLogger(__name__)


class DiscordPoster:
    def __init__(self):
        self._channel_id = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))

    async def post_scouting_digest(
        self,
        candidates: list[dict],
        channel_id: int | None = None,
    ) -> None:
        try:
            target_id = channel_id or self._channel_id

            # bot.get_channel() only works if bot is connected
            # Try get_channel first, fall back to fetch_channel
            channel = bot.get_channel(target_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(target_id)
                except Exception:
                    logger.warning(
                        f"Channel {target_id} introuvable — "
                        f"bot connecté: {not bot.is_closed()}"
                    )
                    return

            for candidate in candidates:
                embed = discord.Embed(
                    title=candidate.get("name", "Jeu inconnu"),
                    url=candidate.get("url", ""),
                    description=candidate.get("description", "")[:200],
                    color=0x5865F2,
                )
                embed.add_field(
                    name="Score",
                    value=str(candidate.get("score", 0)),
                    inline=True,
                )
                embed.add_field(
                    name="Source",
                    value=candidate.get("source", "N/A"),
                    inline=True,
                )
                embed.add_field(
                    name="Genres",
                    value=", ".join(candidate.get("genres", [])) or "N/A",
                    inline=True,
                )
                embed.add_field(
                    name="Release Date",
                    value=candidate.get("release_date", "N/A"),
                    inline=True,
                )
                if candidate.get("signal"):
                    embed.add_field(
                        name="Signal",
                        value=candidate["signal"],
                        inline=False,
                    )
                embed.set_footer(text="NAGA Scout Bot • Auto-scouting 🤖")

                if candidate.get("url"):
                    thumbnail = (
                        "https://cdn.akamai.steamstatic.com/steam/apps/"
                        f"{candidate.get('app_id', 0)}/header.jpg"
                    )
                    embed.set_thumbnail(url=thumbnail)

                view = discord.ui.View()
                if candidate.get("url"):
                    view.add_item(
                        discord.ui.Button(
                            label="Voir sur Steam",
                            url=candidate["url"],
                            style=discord.ButtonStyle.link,
                        )
                    )

                msg = await channel.send(embed=embed, view=view)
                for emoji in ["👍", "👎", "🔥", "❤️"]:
                    await msg.add_reaction(emoji)

        except Exception as exc:
            logger.error(f"Erreur post_scouting_digest: {exc}", exc_info=True)

    async def post_momentum_alert(
        self,
        game_name: str,
        momentum_score: int,
        label: str,
        stat_cle: str,
    ) -> None:
        """Placeholder — à implémenter."""
        logger.info(
            f"[momentum] {game_name} — {label} "
            f"(score {momentum_score}) : {stat_cle}"
        )
