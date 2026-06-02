"""Tests de services/steam.py (fonctions pures, sans appel réseau)."""

import pytest

from services.steam import extract_app_id


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://store.steampowered.com/app/1234567/Game_Name/", 1234567),
        ("https://store.steampowered.com/app/1234567", 1234567),
        ("https://store.steampowered.com/app/292030/The_Witcher_3/", 292030),
    ],
)
def test_extract_app_id_valid(url, expected):
    assert extract_app_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://google.com",
        "not a url",
        "",
        "https://store.steampowered.com/bundle/123/",
    ],
)
def test_extract_app_id_invalid(url):
    assert extract_app_id(url) is None
