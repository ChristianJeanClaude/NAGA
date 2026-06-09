"""Tests de clients/bluesky.py — parsing et logique du client Bluesky.

Aucun appel réseau : ``atproto.AsyncClient`` est remplacé par un faux client
(``types.SimpleNamespace`` + ``AsyncMock``). Les tests vérifient le mapping
``PostView`` → :class:`BlueskyPost`, l'absence de propagation d'erreur, la
déduplication par URI et le filtrage par nombre de likes.
"""

import types
from unittest.mock import AsyncMock

from clients.bluesky import BlueskyClient, BlueskyPost, _to_post


def _fake_view(
    uri,
    *,
    text="hello world",
    handle="dev.bsky.social",
    created_at="2026-06-01T12:00:00.000Z",
    likes=0,
    reposts=0,
    replies=0,
):
    """Construit une fausse ``PostView`` atproto via SimpleNamespace."""
    return types.SimpleNamespace(
        uri=uri,
        author=types.SimpleNamespace(handle=handle, did="did:plc:abc123"),
        record=types.SimpleNamespace(text=text, created_at=created_at),
        like_count=likes,
        repost_count=reposts,
        reply_count=replies,
    )


def _client_returning(*views, side_effect=None):
    """Crée un BlueskyClient dont ``search_posts`` renvoie ``views``.

    Si ``side_effect`` est fourni (ex. une exception), il prime sur la valeur de
    retour — utile pour simuler une panne réseau.
    """
    client = BlueskyClient()
    response = types.SimpleNamespace(posts=list(views))
    if side_effect is not None:
        search = AsyncMock(side_effect=side_effect)
    else:
        search = AsyncMock(return_value=response)
    client._client = types.SimpleNamespace(
        app=types.SimpleNamespace(
            bsky=types.SimpleNamespace(
                feed=types.SimpleNamespace(search_posts=search)
            )
        )
    )
    return client, search


def test_to_post_populates_fields():
    view = _fake_view(
        "at://did:plc:abc123/app.bsky.feed.post/rkey789",
        text="Check my #indiegame devlog! #gamedev",
        handle="me.bsky.social",
        likes=42,
        reposts=7,
        replies=3,
    )
    post = _to_post(view)

    assert isinstance(post, BlueskyPost)
    assert post.id == "at://did:plc:abc123/app.bsky.feed.post/rkey789"
    assert post.text == "Check my #indiegame devlog! #gamedev"
    assert post.author == "me.bsky.social"
    assert post.created_at == "2026-06-01T12:00:00.000Z"
    assert post.like_count == 42
    assert post.repost_count == 7
    assert post.reply_count == 3
    assert post.url == "https://bsky.app/profile/me.bsky.social/post/rkey789"
    assert post.hashtags == ["indiegame", "gamedev"]


def test_to_post_defaults_when_counts_missing():
    # Une vue sans compteurs ni record → valeurs par défaut, pas d'erreur.
    view = types.SimpleNamespace(
        uri="at://did/app.bsky.feed.post/x",
        author=types.SimpleNamespace(handle="h.bsky.social"),
        record=None,
    )
    post = _to_post(view)
    assert post.text == ""
    assert post.like_count == 0
    assert post.hashtags == []
    assert post.url == "https://bsky.app/profile/h.bsky.social/post/x"


async def test_search_posts_returns_empty_on_exception():
    client, _ = _client_returning(side_effect=RuntimeError("network down"))
    # Tous les hashtags échouent → liste vide, jamais d'exception propagée.
    result = await client.search_posts(["indiegame"])
    assert result == []


async def test_search_posts_dedupes_by_uri():
    same_uri = "at://did/app.bsky.feed.post/dup"
    view = _fake_view(same_uri, likes=5)
    client, search = _client_returning(view)

    # Deux hashtags renvoient le MÊME post → une seule entrée après dédup.
    result = await client.search_posts(["indiegame", "gamedev"])

    assert search.await_count == 2
    assert len(result) == 1
    assert result[0].id == same_uri


async def test_search_posts_sorted_by_likes_desc():
    low = _fake_view("at://did/app.bsky.feed.post/low", likes=2)
    high = _fake_view("at://did/app.bsky.feed.post/high", likes=99)
    client, _ = _client_returning(low, high)

    result = await client.search_posts(["indiegame"])

    assert [p.like_count for p in result] == [99, 2]


async def test_get_trending_posts_filters_by_min_likes():
    below = _fake_view("at://did/app.bsky.feed.post/below", likes=5)
    above = _fake_view("at://did/app.bsky.feed.post/above", likes=20)
    client, _ = _client_returning(below, above)

    result = await client.get_trending_posts(["indiegame"], min_likes=10)

    assert len(result) == 1
    assert result[0].id == "at://did/app.bsky.feed.post/above"
    assert result[0].like_count == 20
