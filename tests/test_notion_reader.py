"""Tests de notion/reader.py — répartition hebdomadaire et parsing (sans réseau).

Aucun appel réseau réel : ``get_all_games`` est remplacé par un mock renvoyant
une liste fixe de jeux.
"""

from unittest.mock import AsyncMock

from notion.reader import NotionReader, TrackedGame, _extract_twitter_handle


def _make_games(count: int) -> list[TrackedGame]:
    """Crée ``count`` jeux dont les page_id se trient dans l'ordre de l'index."""
    return [
        TrackedGame(
            page_id=f"{i:02d}",
            name=f"Game {i}",
            steam_app_id=1000 + i,
            twitter_handle=None,
            steam_url=None,
        )
        for i in range(count)
    ]


def _reader_with_games(games: list[TrackedGame]) -> NotionReader:
    reader = NotionReader()
    reader.get_all_games = AsyncMock(return_value=games)
    return reader


async def test_get_games_to_track_today_day_0():
    reader = _reader_with_games(_make_games(14))
    result = await reader.get_games_to_track_today(day_index=0)
    assert [g.page_id for g in result] == ["00", "07"]


async def test_get_games_to_track_today_day_1():
    reader = _reader_with_games(_make_games(14))
    result = await reader.get_games_to_track_today(day_index=1)
    assert [g.page_id for g in result] == ["01", "08"]


async def test_get_games_to_track_today_day_6():
    reader = _reader_with_games(_make_games(14))
    result = await reader.get_games_to_track_today(day_index=6)
    assert [g.page_id for g in result] == ["06", "13"]


async def test_get_games_to_track_today_empty():
    reader = _reader_with_games([])
    assert await reader.get_games_to_track_today(day_index=0) == []


def test_extract_twitter_handle_x_com():
    assert _extract_twitter_handle("https://x.com/NomStudio") == "@NomStudio"


def test_extract_twitter_handle_twitter_com():
    assert _extract_twitter_handle("https://twitter.com/NomStudio") == "@NomStudio"


def test_extract_twitter_handle_none():
    assert _extract_twitter_handle(None) is None
