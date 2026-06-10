"""Tests de models/game.py — parsing des dates Steam."""

import pytest

from models.game import GameData


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("21 Sep, 2023", "2023-09-21"),
        ("21 September, 2023", "2023-09-21"),
        ("Sep 2023", "2023-09-01"),
        ("September 2023", "2023-09-01"),
        ("2023", "2023-01-01"),
    ],
)
def test_parse_steam_date_valid(raw, expected):
    assert GameData._parse_steam_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Coming soon",
        "",
        "TBD",
    ],
)
def test_parse_steam_date_invalid(raw):
    assert GameData._parse_steam_date(raw) is None


def _make_game(**overrides) -> GameData:
    """Construit un GameData minimal, surchargeable par champ."""
    base = dict(
        app_id=1,
        steam_url="",
        name="",
        short_description="",
        release_date=None,
        developer="",
        publisher="",
        scouted_by="",
        scouted_at="",
        discord_message_url="",
    )
    base.update(overrides)
    return GameData(**base)


def test_to_notion_scouted_at_iso():
    game = _make_game(scouted_at="2026-06-03T12:30:00+00:00")
    props = game.to_notion_properties()
    assert props["Scouted At"] == {"date": {"start": "2026-06-03T12:30:00+00:00"}}


def test_to_notion_scouted_at_empty_omitted():
    game = _make_game(scouted_at="")
    assert "Scouted At" not in game.to_notion_properties()


def test_to_notion_trailer_url():
    game = _make_game(trailer="https://cdn/movie.webm")
    assert game.to_notion_properties()["Trailer"] == {"url": "https://cdn/movie.webm"}


def test_to_notion_trailer_none_omitted():
    game = _make_game(trailer=None)
    assert "Trailer" not in game.to_notion_properties()


def test_to_notion_followers_number():
    game = _make_game(followers=12345)
    assert game.to_notion_properties()["Followers"] == {"number": 12345}


def test_to_notion_followers_none_omitted():
    game = _make_game(followers=None)
    assert "Followers" not in game.to_notion_properties()


def test_to_notion_release_date_parsed_to_iso():
    game = _make_game(release_date="21 Sep, 2023")
    assert game.to_notion_properties()["Release Date"] == {
        "date": {"start": "2023-09-21"}
    }


def test_to_notion_release_date_unparseable_omitted():
    game = _make_game(release_date="Coming soon")
    assert "Release Date" not in game.to_notion_properties()
