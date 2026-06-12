"""ScoutingJob — détection automatique de jeux indés à scouter.

Pipeline quotidien : agrège des candidats depuis trois sources (Steam Coming
Soon, Bluesky, Reddit), les score selon la source et l'engagement, les
déduplique contre la base Notion et la table ``posts_seen``, puis pousse le
top ``TOP_N`` dans Discord. Chaque source est best-effort : son échec n'empêche
pas les autres ni l'exécution globale.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import aiosqlite
from notion_client import AsyncClient

from clients.bluesky import BlueskyClient, BlueskyPost
from notifier.poster import DiscordPoster
from notion.reader import NotionReader
from services.cache import init_db
from services.notion import get_all_app_ids
from services.steam import HEADERS, STEAM_STORE_URL, extract_app_id
from services.suggest import (
    _fetch_basic_info,
    get_naga_profile,
    search_steam_suggestions,
)

logger = logging.getLogger(__name__)

TOP_N = 3                    # candidats poussés par run
SCORE_FLOOR = 10             # score minimum pour être proposé
STEAM_FOLLOWERS_CAP = 2000   # exclure les jeux > 2000 followers Steam
DB_PATH = Path("db/scouting.db")

_STEAMSPY_URL = "https://steamspy.com/api.php"


class ScoutingJob:
    """Agrège, score et publie des candidats de scouting (Steam/Bluesky/Reddit)."""

    def __init__(self):
        self.reader = NotionReader()
        self.discord = DiscordPoster()
        self.db_path = DB_PATH
        self._notion_client = AsyncClient(auth=os.environ.get("NOTION_TOKEN", ""))

    async def run(self) -> None:
        """Pipeline principal du ScoutingJob.

        1. Charge le profil NAGA depuis Notion.
        2. Récupère les app_ids déjà connus (Notion + posts_seen).
        3. Collecte les candidats depuis Steam, Bluesky et Reddit (best-effort).
        4. Score (réalisé par source lors de la collecte).
        5. Déduplique contre ``posts_seen`` (et par app_id entre sources).
        6. Garde le top ``TOP_N`` (score ≥ ``SCORE_FLOOR``).
        7. Poste dans Discord.
        8. Marque les candidats retenus comme vus.
        """
        await init_db()
        now = datetime.now(timezone.utc)

        profile = await get_naga_profile(
            self._notion_client, os.environ.get("NOTION_DATABASE_ID", "")
        )
        known_ids = await self._known_app_ids()

        candidates: list[dict] = []
        candidates += await self._collect_steam_candidates(profile, known_ids)
        candidates += await self._collect_bluesky_candidates(known_ids)
        candidates += await self._collect_reddit_candidates(known_ids)

        # 5a. Déduplication par app_id entre sources : on garde le meilleur score.
        by_app: dict[int, dict] = {}
        for candidate in candidates:
            app_id = candidate["app_id"]
            existing = by_app.get(app_id)
            if existing is None or candidate["score"] > existing["score"]:
                by_app[app_id] = candidate

        # 5b. Déduplication contre l'historique ``posts_seen``.
        fresh: list[dict] = []
        for candidate in by_app.values():
            if await self._is_seen(candidate["source"], candidate["post_id"]):
                continue
            fresh.append(candidate)

        # 6. Plancher de score + top N.
        fresh = [c for c in fresh if c["score"] >= SCORE_FLOOR]
        fresh.sort(key=lambda c: c["score"], reverse=True)
        top_candidates = fresh[:TOP_N]

        # 7. Aucun candidat quali : digest vide, on ne poste rien.
        if len(top_candidates) == 0:
            logger.info("Aucun candidat quali ce run — digest vide")
            return

        # 8. Publication Discord (uniquement s'il y a des candidats).
        await self.discord.post_scouting_digest(top_candidates)

        # 9. Marquage comme vus.
        for candidate in top_candidates:
            await self._mark_seen(
                candidate["source"],
                candidate["post_id"],
                candidate.get("author", ""),
                now,
            )

        logger.info(
            "ScoutingJob : %d candidat(s) collecté(s), %d retenu(s)",
            len(candidates),
            len(top_candidates),
        )

    async def _collect_steam_candidates(
        self,
        profile: dict,
        known_ids: set[int],
    ) -> list[dict]:
        """Récupère des jeux Steam Coming Soon, filtrés par cap de followers.

        Score de base : 20 (présence Steam Coming Soon). Exclut les jeux dont le
        nombre de followers Steam dépasse ``STEAM_FOLLOWERS_CAP``.
        """
        candidates: list[dict] = []
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                suggestions = await search_steam_suggestions(
                    profile.get("genres", []),
                    profile.get("tags", []),
                    known_ids,
                    session,
                    max_results=10,
                    hashtags=profile.get("hashtags", []),
                )
                for suggestion in suggestions:
                    app_id = suggestion["app_id"]
                    followers = await self._steam_followers(app_id, session)
                    if followers is not None and followers > STEAM_FOLLOWERS_CAP:
                        continue
                    genres = suggestion.get("genres", [])
                    age_bonus, age_reason = self._age_score(
                        app_id, followers or 0
                    )
                    score = 20 + age_bonus  # base 20 + bonus d'âge
                    if age_reason:
                        signal = f"{age_reason} — Coming Soon"
                    else:
                        signal = (
                            "Jeu Coming Soon détecté sur Steam "
                            f"({', '.join(genres[:2])})"
                        )
                    candidates.append(
                        {
                            "source": "steam",
                            "app_id": app_id,
                            "name": suggestion.get("name", ""),
                            "url": suggestion.get("steam_url", ""),
                            "score": score,
                            "description": suggestion.get("short_description", ""),
                            "genres": genres,
                            "release_date": suggestion.get("release_date", "") or "",
                            "post_id": str(app_id),
                            "author": suggestion.get("developer", ""),
                            "signal": signal,
                        }
                    )
        except Exception:
            logger.error("Échec de la collecte Steam", exc_info=True)
            return []
        return candidates

    async def _collect_bluesky_candidates(
        self,
        known_ids: set[int],
    ) -> list[dict]:
        """Scrape Bluesky pour des posts mentionnant des jeux indés.

        Score basé sur l'engagement (likes). Best-effort : retourne [] sur
        n'importe quelle erreur.
        """
        candidates: list[dict] = []
        try:
            async with BlueskyClient() as client:
                posts: list[BlueskyPost] = await client.get_trending_posts(
                    min_likes=5
                )

            async with aiohttp.ClientSession(headers=HEADERS) as session:
                for post in posts:
                    app_id = extract_app_id(f"{post.text} {post.url}")
                    if app_id is None or app_id in known_ids:
                        continue

                    score = 10
                    if post.like_count >= 10:
                        score += 10
                    if post.like_count >= 50:
                        score += 20
                    if post.like_count >= 100:
                        score += 30

                    name, url = await self._name_and_url(app_id, session)
                    signal = (
                        f"Post Bluesky — {post.like_count} likes (@{post.author})"
                    )
                    candidates.append(
                        {
                            "source": "bluesky",
                            "app_id": app_id,
                            "name": name,
                            "url": url,
                            "score": score,
                            "description": post.text[:200],
                            "genres": [],
                            "release_date": "",
                            "post_id": post.id,
                            "author": post.author,
                            "signal": signal,
                        }
                    )
        except Exception:
            logger.error("Échec de la collecte Bluesky", exc_info=True)
            return []
        return candidates

    async def _collect_reddit_candidates(
        self,
        known_ids: set[int],
    ) -> list[dict]:
        """Scrape Reddit pour des posts mentionnant des jeux indés.

        Entièrement ignoré si ``REDDIT_CLIENT_ID`` est absent. Score basé sur le
        score Reddit, avec bonus si le même jeu apparaît dans 2+ subreddits.
        Best-effort : retourne [] sur n'importe quelle erreur.
        """
        if not os.environ.get("REDDIT_CLIENT_ID"):
            logger.info("ScoutingJob : Reddit ignoré (REDDIT_CLIENT_ID absent)")
            return []

        candidates: list[dict] = []
        try:
            # Import paresseux : n'importe asyncpraw que si Reddit est configuré.
            from clients.reddit import RedditClient

            async with RedditClient() as client:
                posts = await client.search_posts()

            # Regroupe les posts par app_id Steam extrait.
            by_app: dict[int, list] = {}
            for post in posts:
                blob = f"{post.url} {post.title} {post.selftext}"
                app_id = extract_app_id(blob)
                if app_id is None or app_id in known_ids:
                    continue
                by_app.setdefault(app_id, []).append(post)

            async with aiohttp.ClientSession(headers=HEADERS) as session:
                for app_id, app_posts in by_app.items():
                    best = max(app_posts, key=lambda p: p.score)

                    score = 10
                    if best.score >= 10:
                        score += 10
                    if best.score >= 100:
                        score += 20
                    if best.score >= 500:
                        score += 30
                    if len({p.subreddit for p in app_posts}) >= 2:
                        score += 30

                    name, url = await self._name_and_url(app_id, session)
                    description = (best.selftext or best.title or "")[:200]
                    signal = (
                        f"Post Reddit — score {best.score} (r/{best.subreddit})"
                    )
                    candidates.append(
                        {
                            "source": "reddit",
                            "app_id": app_id,
                            "name": name,
                            "url": url,
                            "score": score,
                            "description": description,
                            "genres": [],
                            "release_date": "",
                            "post_id": best.id,
                            "author": best.author,
                            "signal": signal,
                        }
                    )
        except Exception:
            logger.error("Échec de la collecte Reddit", exc_info=True)
            return []
        return candidates

    def _age_score(self, app_id: int, followers: int) -> tuple[int, str]:
        """Score basé sur le ratio followers/âge de la page Steam.

        Utilise l'App ID comme proxy d'âge (les IDs sont séquentiels) : plus
        de followers sur une page récente vaut davantage que les mêmes
        followers sur une page ancienne.

        Tranches d'âge :
        - app_id >= 3_000_000 → très récente (2024-2025)
        - app_id >= 2_000_000 → récente (2022-2023)
        - app_id >= 1_000_000 → moyenne (2019-2021)
        - app_id <  1_000_000 → ancienne (avant 2019)

        Retourne ``(score, reason)``. ``reason`` est vide si ``score == 0``.
        """
        if app_id >= 3_000_000:
            label = "page très récente"
            if followers >= 500:
                score = 40
            elif followers >= 100:
                score = 25
            elif followers >= 50:
                score = 15
            elif followers > 0:
                score = 5
            else:
                score = 0
        elif app_id >= 2_000_000:
            label = "page récente"
            if followers >= 2000:
                score = 30
            elif followers >= 500:
                score = 20
            elif followers >= 100:
                score = 10
            else:
                score = 0
        elif app_id >= 1_000_000:
            label = "page Steam"
            if followers >= 5000:
                score = 20
            elif followers >= 2000:
                score = 10
            else:
                score = 0
        else:
            label = "page ancienne"
            if followers >= 10000:
                score = 10
            else:
                score = 0

        if score == 0:
            return 0, ""
        reason = f"{label} (app #{app_id}) avec {followers} followers"
        return score, reason

    async def _steam_followers(
        self, app_id: int, session: aiohttp.ClientSession
    ) -> int | None:
        """Nombre de followers Steam via SteamSpy (None si indisponible)."""
        params = {"request": "appdetails", "appid": app_id}
        try:
            async with session.get(
                _STEAMSPY_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception:
            return None
        followers = data.get("followers") if isinstance(data, dict) else None
        return followers if isinstance(followers, int) else None

    async def _name_and_url(
        self, app_id: int, session: aiohttp.ClientSession
    ) -> tuple[str, str]:
        """Résout (nom, URL Steam) pour un app_id, best-effort via appdetails."""
        url = STEAM_STORE_URL.format(app_id=app_id)
        name = f"Jeu Steam {app_id}"
        try:
            info = await _fetch_basic_info(app_id, session)
            if info:
                name = info.get("name") or name
                url = info.get("steam_url") or url
        except Exception:
            pass
        return name, url

    async def _known_app_ids(self) -> set[int]:
        """App_ids déjà connus : base Notion + posts_seen (plateforme steam)."""
        known = await get_all_app_ids()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT post_id FROM posts_seen WHERE platform = 'steam'"
                ) as cursor:
                    rows = await cursor.fetchall()
            for (post_id,) in rows:
                try:
                    known.add(int(post_id))
                except (TypeError, ValueError):
                    continue
        except Exception:
            logger.error(
                "Échec de la lecture de posts_seen (steam)", exc_info=True
            )
        return known

    async def _is_seen(self, platform: str, post_id: str) -> bool:
        """Indique si ``(platform, post_id)`` a déjà été posté."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT 1 FROM posts_seen WHERE platform = ? AND post_id = ?",
                    (platform, post_id),
                ) as cursor:
                    return await cursor.fetchone() is not None
        except Exception:
            return False

    async def _mark_seen(
        self,
        platform: str,
        post_id: str,
        author: str,
        now: datetime,
    ) -> None:
        """Enregistre ``(platform, post_id)`` dans ``posts_seen`` (idempotent)."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO posts_seen "
                    "(platform, post_id, author_id, seen_at) "
                    "VALUES (?, ?, ?, ?)",
                    (platform, post_id, author, now.isoformat()),
                )
                await db.commit()
        except Exception:
            logger.error(
                "Échec du marquage posts_seen (%s/%s)",
                platform,
                post_id,
                exc_info=True,
            )

    def close(self) -> None:
        pass
