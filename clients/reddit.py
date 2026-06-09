"""Client Reddit async pour le scouting de jeux indés."""

import os
from dataclasses import dataclass

import asyncpraw
import asyncprawcore

# Subreddits scrutés par défaut
DEFAULT_SUBREDDITS = [
    "indiegaming",
    "gamedev",
    "IndieDev",
    "unreleased_games",
    "playmygame",
]

# Mots-clés de recherche
DEFAULT_KEYWORDS = [
    "indie game",
    "devlog",
    "solo dev",
    "coming soon",
    "steam page",
]


@dataclass
class RedditPost:
    id: str
    title: str
    url: str
    score: int
    author: str
    subreddit: str
    created_utc: float
    selftext: str = ""


class RedditClient:
    """
    Client async Reddit via asyncpraw.

    Requires env vars:
    - REDDIT_CLIENT_ID
    - REDDIT_CLIENT_SECRET
    - REDDIT_USER_AGENT (default: "naga-scout-bot/1.0")

    Usage:
        async with RedditClient() as client:
            posts = await client.search_posts(subreddits, keywords)
    """

    def __init__(self):
        self._reddit = asyncpraw.Reddit(
            client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
            client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
            user_agent=os.environ.get("REDDIT_USER_AGENT", "naga-scout-bot/1.0"),
        )

    async def __aenter__(self) -> "RedditClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Ferme la session HTTP sous-jacente d'asyncpraw."""
        await self._reddit.close()

    async def search_posts(
        self,
        subreddits: list[str] | None = None,
        keywords: list[str] | None = None,
        *,
        limit: int = 25,
    ) -> list[RedditPost]:
        """Recherche les posts correspondant aux mots-clés dans les subreddits.

        Les mots-clés sont combinés en une requête ``OR`` et appliqués à chaque
        subreddit. Les résultats sont dédupliqués par ``id`` (un même post peut
        remonter pour plusieurs mots-clés).

        Best-effort : un subreddit inaccessible (privé, banni, quota dépassé)
        est ignoré sans interrompre le balayage des autres. ``limit`` borne le
        nombre de résultats récupérés *par subreddit*.
        """
        subreddits = subreddits or DEFAULT_SUBREDDITS
        keywords = keywords or DEFAULT_KEYWORDS
        query = " OR ".join(f'"{kw}"' for kw in keywords)

        posts: dict[str, RedditPost] = {}
        for name in subreddits:
            try:
                subreddit = await self._reddit.subreddit(name)
                async for submission in subreddit.search(
                    query, sort="new", time_filter="month", limit=limit
                ):
                    post = _to_post(submission)
                    posts[post.id] = post
            except asyncprawcore.AsyncPrawcoreException:
                # Subreddit indisponible : on passe au suivant.
                continue

        return list(posts.values())


def _to_post(submission) -> RedditPost:
    """Convertit une soumission asyncpraw en :class:`RedditPost`.

    ``author`` vaut ``"[deleted]"`` si l'auteur a supprimé son compte (l'objet
    ``submission.author`` est alors ``None``).
    """
    return RedditPost(
        id=submission.id,
        title=submission.title,
        url=submission.url,
        score=submission.score,
        author=str(submission.author) if submission.author else "[deleted]",
        subreddit=submission.subreddit.display_name,
        created_utc=submission.created_utc,
        selftext=submission.selftext or "",
    )
