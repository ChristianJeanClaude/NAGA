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
