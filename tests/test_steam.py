"""Tests de services/steam.py (fonctions pures, sans appel réseau)."""

import pytest

from services.steam import (
    _decode_steam_link,
    _extract_screenshots,
    _extract_trailer,
    extract_app_id,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://store.steampowered.com/app/1234567/Game_Name/", 1234567),
        ("https://store.steampowered.com/app/1234567", 1234567),
        ("https://store.steampowered.com/app/292030/The_Witcher_3/", 292030),
        # Query params et fragments doivent être ignorés.
        (
            "https://store.steampowered.com/app/1067360/Pax_Autocratica/?l=french",
            1067360,
        ),
        (
            "https://store.steampowered.com/app/1245620/ELDEN_RING/?curator_clanid=4789",
            1245620,
        ),
        ("https://store.steampowered.com/app/292030?utm_source=discord", 292030),
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


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        # Lien Discord encapsulé par le redirecteur Steam.
        (
            "https://steamcommunity.com/linkfilter/?u=https%3A%2F%2Fdiscord.gg%2Fabc123",
            "https://discord.gg/abc123",
        ),
        # Lien Twitter/X encapsulé, avec paramètres de requête dans la cible.
        (
            "https://steamcommunity.com/linkfilter/?u=https%3A%2F%2Ftwitter.com%2Fstudio%3Flang%3Dfr",
            "https://twitter.com/studio?lang=fr",
        ),
        # URL directe (non encapsulée) : retournée inchangée.
        ("https://discord.gg/abc123", "https://discord.gg/abc123"),
        # linkfilter sans paramètre ``u`` : retourné inchangé.
        (
            "https://steamcommunity.com/linkfilter/",
            "https://steamcommunity.com/linkfilter/",
        ),
        # Entrée vide : retournée telle quelle sans planter.
        ("", ""),
    ],
)
def test_decode_steam_link(href, expected):
    assert _decode_steam_link(href) == expected


def test_extract_screenshots_takes_first_three():
    data = {
        "screenshots": [
            {"id": i, "path_full": f"https://cdn/ss_{i}.jpg"} for i in range(5)
        ]
    }
    assert _extract_screenshots(data) == [
        "https://cdn/ss_0.jpg",
        "https://cdn/ss_1.jpg",
        "https://cdn/ss_2.jpg",
    ]


def test_extract_screenshots_skips_malformed_and_missing():
    data = {
        "screenshots": [
            {"path_full": "https://cdn/ok.jpg"},
            {"id": 1},  # pas de path_full
            "not a dict",
            {"path_full": ""},  # vide
        ]
    }
    assert _extract_screenshots(data) == ["https://cdn/ok.jpg"]


def test_extract_screenshots_absent():
    assert _extract_screenshots({}) == []


def test_extract_trailer_prefers_webm():
    data = {
        "movies": [
            {
                "webm": {"max": "https://cdn/movie.webm"},
                "mp4": {"max": "https://cdn/movie.mp4"},
            }
        ]
    }
    assert _extract_trailer(data) == "https://cdn/movie.webm"


def test_extract_trailer_falls_back_to_mp4():
    data = {"movies": [{"mp4": {"max": "https://cdn/movie.mp4"}}]}
    assert _extract_trailer(data) == "https://cdn/movie.mp4"


def test_extract_trailer_absent():
    assert _extract_trailer({}) is None
    assert _extract_trailer({"movies": []}) is None
