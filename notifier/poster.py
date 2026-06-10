"""Publication des résultats de scouting/tracking dans Discord.

Poste un digest des candidats détectés par le ScoutingJob via un webhook
Discord (``DISCORD_SCOUT_WEBHOOK``). À défaut de webhook configuré, les
candidats sont simplement journalisés (repli best-effort). Aucune méthode ne
propage d'exception : un échec de notification ne doit jamais faire échouer le
job appelant.
"""

import logging
import os

import aiohttp
import discord as discord_lib

logger = logging.getLogger(__name__)


class DiscordPoster:
    def __init__(self):
        self._webhook_url = os.environ.get("DISCORD_SCOUT_WEBHOOK", "")

    async def post_scouting_digest(
        self,
        candidates: list[dict],
        channel_id: int | None = None,
    ) -> None:
        """
        Poste les candidats dans Discord via webhook ou channel.

        Pour chaque candidat, crée un embed avec :
        - title = name
        - url = steam_url
        - description = description[:200]
        - fields: Score, Source, Genres, Release Date
        - color = 0x5865F2
        - footer = "NAGA Scout Bot • Auto-scouting 🤖"

        Utilise DISCORD_SCOUT_WEBHOOK si disponible,
        sinon log les candidats (best-effort fallback).
        Never raises.
        """
        if not candidates:
            return

        # Repli : pas de webhook configuré → on journalise seulement.
        if not self._webhook_url:
            for candidate in candidates:
                logger.info(
                    "[scouting] %s (score %s, source %s) — %s",
                    candidate.get("name"),
                    candidate.get("score"),
                    candidate.get("source"),
                    candidate.get("url"),
                )
            return

        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord_lib.Webhook.from_url(
                    self._webhook_url, session=session
                )
                for candidate in candidates:
                    await webhook.send(embed=self._build_embed(candidate))
        except Exception:
            logger.error(
                "Échec de la publication du digest de scouting", exc_info=True
            )

    @staticmethod
    def _build_embed(candidate: dict) -> "discord_lib.Embed":
        """Construit l'embed Discord pour un candidat."""
        url = candidate.get("url") or None
        embed = discord_lib.Embed(
            title=candidate.get("name", "") or "Sans titre",
            url=url,
            description=(candidate.get("description", "") or "")[:200],
            color=0x5865F2,
        )
        embed.add_field(
            name="Score", value=str(candidate.get("score", "?")), inline=True
        )
        embed.add_field(
            name="Source", value=candidate.get("source", "?"), inline=True
        )
        genres = candidate.get("genres") or []
        embed.add_field(
            name="Genres", value=", ".join(genres) or "N/A", inline=True
        )
        embed.add_field(
            name="Release Date",
            value=candidate.get("release_date") or "N/A",
            inline=True,
        )
        embed.set_footer(text="NAGA Scout Bot • Auto-scouting 🤖")
        return embed

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
