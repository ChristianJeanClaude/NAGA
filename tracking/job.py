"""TrackingJob — suivi hebdomadaire du momentum (Steam uniquement).

Chaque jour, traite 1/7 des jeux de la base Notion (réparti par day_index).
Pour chaque jeu : relève le nombre de followers Steam, l'enregistre comme
snapshot, le compare au plus ancien snapshot connu (baseline) et, si la
croissance dépasse le seuil, met à jour le momentum dans Notion et émet une
alerte Discord (best-effort).

Twitter n'est pas encore pris en charge.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite

from notion.reader import NotionReader, TrackedGame
from notion.writer import NotionWriter
from services.cache import init_db

# Pas de SteamClient dédié : on réutilise directement le scraping de la page
# boutique (``follower_count``) déjà implémenté dans services.steam.
from services.steam import HEADERS, _scrape_store_page

# Import paresseux : le poster Discord n'est pas encore implémenté. On le rend
# optionnel pour que le job tourne sans lui (alertes simplement journalisées).
try:
    from discord.poster import DiscordPoster
except Exception:  # pragma: no cover - module optionnel, absent pour l'instant
    DiscordPoster = None

logger = logging.getLogger(__name__)

GROWTH_THRESHOLD = 0.20   # +20% déclenche une alerte
COLD_START_DAYS = 14      # < 2 semaines d'historique → skip
HOT_THRESHOLD = 0.50      # >= +50% → "Hot", sinon "Rising"
DB_PATH = Path("db/scouting.db")


class TrackingJob:
    """Suivi quotidien du momentum d'1/7 des jeux scoutés (Steam uniquement)."""

    def __init__(self):
        self.reader = NotionReader()
        self.writer = NotionWriter()
        self.db_path = DB_PATH
        # Poster Discord optionnel (best-effort) : None si indisponible.
        self.poster = None
        if DiscordPoster is not None:
            try:
                self.poster = DiscordPoster()
            except Exception:
                logger.error(
                    "Impossible d'initialiser DiscordPoster", exc_info=True
                )

    async def run(self) -> None:
        """Point d'entrée principal.

        1. Récupère le day_index (jour de la semaine, lundi=0).
        2. Sélectionne les jeux à suivre aujourd'hui (1/7 de la base).
        3. Traite chaque jeu via ``_process_game`` (erreurs isolées par jeu).
        4. Journalise le nombre de jeux traités.
        """
        await init_db()
        now = datetime.now(timezone.utc)
        day_index = now.weekday()

        games = await self.reader.get_games_to_track_today(day_index)
        for game in games:
            try:
                await self._process_game(game, now)
            except Exception:
                logger.error(
                    "Échec du traitement du jeu '%s'", game.name, exc_info=True
                )

        logger.info(
            "TrackingJob : %d jeu(x) traité(s) (jour %d)", len(games), day_index
        )

    async def _process_game(self, game: TrackedGame, now: datetime) -> None:
        """Traite un jeu : snapshot, comparaison au baseline, alerte éventuelle.

        Skip si pas d'App ID Steam, si les followers sont indisponibles, ou si
        l'historique est trop court (cold start). Au-delà, calcule la croissance
        relative depuis le plus ancien snapshot ; si elle atteint
        ``GROWTH_THRESHOLD``, met à jour Notion et émet une alerte.
        """
        if game.steam_app_id is None:
            logger.debug("Skip '%s' : pas d'App ID Steam", game.name)
            return

        followers = await self._get_steam_followers(game.steam_app_id)
        if followers is None:
            logger.warning(
                "Followers Steam indisponibles pour '%s' (app_id=%s)",
                game.name,
                game.steam_app_id,
            )
            return

        # 1. Enregistre le snapshot du jour AVANT de calculer le baseline (sur la
        #    toute première exécution, baseline == snapshot courant → cold start).
        await self._save_snapshot(game, followers, now)

        # 2. Baseline = plus ancien snapshot connu pour ce jeu.
        baseline = await self._get_baseline(game.steam_app_id)
        if baseline is None:
            return
        baseline_followers, baseline_at = baseline

        # 3. Cold start : pas assez d'historique pour conclure → on attend.
        history_days = (now - baseline_at).days
        if history_days < COLD_START_DAYS:
            logger.info(
                "Cold start pour '%s' : %d j d'historique (< %d)",
                game.name,
                history_days,
                COLD_START_DAYS,
            )
            return

        if baseline_followers <= 0:
            return

        # 4. Croissance relative depuis le baseline.
        growth = (followers - baseline_followers) / baseline_followers
        if growth < GROWTH_THRESHOLD:
            return

        momentum_score = round(growth * 100)
        label = "Hot" if growth >= HOT_THRESHOLD else "Rising"
        stat_cle = (
            f"+{growth * 100:.0f}% followers "
            f"({baseline_followers:,} → {followers:,})"
        )

        ok = await self.writer.update_game(
            page_id=game.page_id,
            momentum_score=momentum_score,
            stat_cle=stat_cle,
            lien_post=game.steam_url or "",
        )
        if not ok:
            logger.error("Échec de la mise à jour Notion pour '%s'", game.name)

        await self._send_alert(game, momentum_score, label, stat_cle)

    async def _get_steam_followers(self, app_id: int) -> int | None:
        """Relève le nombre de followers Steam via le scraping de la page boutique.

        Retourne ``None`` si le scraping échoue ou si le compteur est absent.
        """
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                data = await _scrape_store_page(app_id, session)
        except Exception:
            logger.error(
                "Échec du scraping des followers Steam (app_id=%s)",
                app_id,
                exc_info=True,
            )
            return None
        return data.get("follower_count")

    async def _save_snapshot(
        self, game: TrackedGame, followers: int, now: datetime
    ) -> None:
        """Enregistre un snapshot de followers dans la table ``snapshots``."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO snapshots "
                "(platform, account_id, username, followers, extra_json, checked_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "steam",
                    str(game.steam_app_id),
                    game.name,
                    followers,
                    None,
                    now.isoformat(),
                ),
            )
            await db.commit()

    async def _get_baseline(self, app_id: int) -> tuple[int, datetime] | None:
        """Retourne ``(followers, checked_at)`` du plus ancien snapshot Steam.

        Retourne ``None`` si aucun snapshot exploitable n'existe pour ce jeu.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT followers, checked_at FROM snapshots "
                "WHERE platform = 'steam' AND account_id = ? "
                "AND followers IS NOT NULL "
                "ORDER BY checked_at ASC LIMIT 1",
                (str(app_id),),
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        followers, checked_at = row
        return followers, datetime.fromisoformat(checked_at)

    async def _send_alert(
        self, game: TrackedGame, momentum_score: int, label: str, stat_cle: str
    ) -> None:
        """Émet une alerte Discord (best-effort).

        Si le poster Discord est indisponible, l'alerte est seulement
        journalisée. Une erreur du poster ne fait jamais échouer le job.
        """
        if self.poster is None:
            logger.info(
                "Momentum [%s] '%s' : %s (poster Discord indisponible)",
                label,
                game.name,
                stat_cle,
            )
            return
        try:
            await self.poster.post_momentum_alert(
                game, momentum_score, label, stat_cle
            )
        except Exception:
            logger.error(
                "Échec de l'alerte Discord pour '%s'", game.name, exc_info=True
            )

    def close(self) -> None:
        """Libère les ressources éventuelles (rien à fermer pour l'instant)."""
        pass
