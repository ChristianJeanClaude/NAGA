"""Client Bluesky async pour le scouting de jeux indés."""

import os
import re
from dataclasses import dataclass, field

import atproto

# Hashtags scrutés par défaut
DEFAULT_HASHTAGS = [
    "indiegame",
    "gamedev",
    "indiedev",
    "screenshotsaturday",
    "wishlistwednesday",
]

# Capture les hashtags du texte (lettres, chiffres, underscore), sans le « # ».
_HASHTAG_RE = re.compile(r"#(\w+)")


@dataclass
class BlueskyPost:
    id: str                    # uri du post (at://...)
    text: str                  # contenu textuel
    author: str                # handle de l'auteur
    created_at: str            # ISO 8601
    like_count: int = 0
    repost_count: int = 0
    reply_count: int = 0
    url: str = ""              # lien web vers le post
    hashtags: list[str] = field(default_factory=list)


class BlueskyClient:
    """
    Client async Bluesky via atproto.

    Requires env vars:
    - BLUESKY_HANDLE  (ex: naga.bsky.social)
    - BLUESKY_PASSWORD (app password)

    Usage:
        async with BlueskyClient() as client:
            posts = await client.search_posts(hashtags)

    Note: atproto's async client is AsyncClient.
    Authentication is done once on __aenter__.
    """

    def __init__(self):
        self._handle = os.environ.get("BLUESKY_HANDLE", "")
        self._password = os.environ.get("BLUESKY_PASSWORD", "")
        self._client = atproto.AsyncClient()

    async def __aenter__(self):
        """Authenticate on enter. Skip if credentials are missing."""
        if self._handle and self._password:
            try:
                await self._client.login(self._handle, self._password)
            except Exception:
                pass  # best-effort — continue without auth
        return self

    async def __aexit__(self, *args):
        pass  # atproto AsyncClient has no explicit close

    async def search_posts(
        self,
        hashtags: list[str] = DEFAULT_HASHTAGS,
        limit: int = 25,
    ) -> list[BlueskyPost]:
        """
        Cherche des posts Bluesky pour chaque hashtag listé.

        - Utilise client.app.bsky.feed.search_posts({"q": "#hashtag", "limit": limit})
        - Déduplique par post uri
        - Retourne une liste de BlueskyPost triée par like_count desc
        - Ne raise jamais — retourne [] sur toute erreur
        - Skip les hashtags qui échouent (best-effort)
        """
        deduped: dict[str, BlueskyPost] = {}
        for hashtag in hashtags:
            try:
                response = await self._client.app.bsky.feed.search_posts(
                    {"q": f"#{hashtag}", "limit": limit}
                )
            except Exception:
                # Best-effort : un hashtag en échec ne bloque pas les autres.
                continue

            for view in getattr(response, "posts", None) or []:
                try:
                    post = _to_post(view)
                except Exception:
                    continue  # vue malformée : on l'ignore
                # Premier vu gagne ; un même post peut remonter sur plusieurs tags.
                deduped.setdefault(post.id, post)

        return sorted(deduped.values(), key=lambda p: p.like_count, reverse=True)

    async def get_trending_posts(
        self,
        hashtags: list[str] = DEFAULT_HASHTAGS,
        min_likes: int = 10,
        limit: int = 25,
    ) -> list[BlueskyPost]:
        """
        Retourne les posts avec au moins min_likes likes.
        Filtre les résultats de search_posts.
        """
        posts = await self.search_posts(hashtags, limit=limit)
        return [post for post in posts if post.like_count >= min_likes]


def _rkey_from_uri(uri: str) -> str:
    """Extrait la record-key (dernier segment) d'une URI ``at://``."""
    return uri.rsplit("/", 1)[-1] if uri else ""


def _to_post(view) -> BlueskyPost:
    """Convertit une ``PostView`` atproto en :class:`BlueskyPost`.

    Lecture défensive (``getattr``) : les compteurs absents valent 0 et l'URL
    web est reconstruite depuis le handle de l'auteur et la record-key de l'URI
    (``https://bsky.app/profile/<handle>/post/<rkey>``).
    """
    uri = getattr(view, "uri", "") or ""
    author = getattr(view, "author", None)
    handle = getattr(author, "handle", "") or ""
    record = getattr(view, "record", None)
    text = getattr(record, "text", "") or ""
    created_at = getattr(record, "created_at", "") or ""

    url = ""
    rkey = _rkey_from_uri(uri)
    if handle and rkey:
        url = f"https://bsky.app/profile/{handle}/post/{rkey}"

    return BlueskyPost(
        id=uri,
        text=text,
        author=handle,
        created_at=created_at,
        like_count=getattr(view, "like_count", 0) or 0,
        repost_count=getattr(view, "repost_count", 0) or 0,
        reply_count=getattr(view, "reply_count", 0) or 0,
        url=url,
        hashtags=_HASHTAG_RE.findall(text),
    )
