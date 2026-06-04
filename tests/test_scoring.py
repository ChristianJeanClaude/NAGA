"""Tests de services/scoring.py — score de pertinence (fonction pure)."""

import pytest

from models.game import GameData
from services.scoring import compute_relevance_score


def _make_game(**overrides) -> GameData:
    """Construit un GameData minimal, surchargeable par les champs de métrique."""
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


def test_all_none_returns_zero():
    game = _make_game()
    assert compute_relevance_score(game) == 0


# --- Signaux pré-lancement ---


def test_coming_soon_game_scores_above_zero():
    """Un jeu non sorti mais prometteur (followers + genres + tags) doit scorer."""
    game = _make_game(
        release_date="Coming soon",
        followers=6000,  # 22
        genres=["Indie", "Adventure"],  # 10
        tags=["Cozy", "2D"],  # 8
    )
    assert compute_relevance_score(game) == 40


@pytest.mark.parametrize(
    ("followers", "expected"),
    [
        (10_000, 30),
        (5_000, 22),
        (2_000, 15),
        (500, 8),
        (1, 3),
        (0, 0),
        (None, 0),
    ],
)
def test_followers_thresholds(followers, expected):
    assert compute_relevance_score(_make_game(followers=followers)) == expected


def test_genres_capped_at_25():
    # Les 6 genres NAGA donneraient 30, plafonné à 25.
    game = _make_game(
        genres=["Indie", "Adventure", "Casual", "RPG", "Simulation", "Puzzle"]
    )
    assert compute_relevance_score(game) == 25


def test_genres_ignores_unmatched():
    game = _make_game(genres=["Action", "Indie", "Sports"])  # seul Indie compte
    assert compute_relevance_score(game) == 5


def test_tags_capped_at_20():
    # 10 tags NAGA donneraient 40, plafonné à 20.
    game = _make_game(
        tags=[
            "Cute",
            "Relaxing",
            "Cozy",
            "Singleplayer",
            "Hand-drawn",
            "Colorful",
            "Cartoony",
            "Atmospheric",
            "Story Rich",
            "2D",
        ]
    )
    assert compute_relevance_score(game) == 20


@pytest.mark.parametrize(
    ("discord_url", "twitter_url", "expected"),
    [
        ("https://discord.gg/x", None, 8),
        (None, "https://twitter.com/x", 7),
        ("https://discord.gg/x", "https://twitter.com/x", 15),
        (None, None, 0),
    ],
)
def test_social_links(discord_url, twitter_url, expected):
    game = _make_game(discord_url=discord_url, twitter_url=twitter_url)
    assert compute_relevance_score(game) == expected


@pytest.mark.parametrize(
    ("release_date", "expected"),
    [
        ("21 Sep, 2023", 10),
        ("September 2023", 10),
        ("Coming soon", 0),
        (None, 0),
    ],
)
def test_release_date_known(release_date, expected):
    assert compute_relevance_score(_make_game(release_date=release_date)) == expected


# --- Signaux post-lancement ---


def test_post_launch_metrics():
    game = _make_game(
        review_score="Very Positive",  # 16
        owners_estimate="500,000 .. 1,000,000",  # 12
        peak_ccu=15000,  # 8
        review_count=6000,  # 8
    )
    assert compute_relevance_score(game) == 44


def test_low_post_launch_metrics():
    game = _make_game(
        review_score="Mixed",  # 6
        owners_estimate="20,000 .. 50,000",  # 0 (borne < 50k)
        peak_ccu=500,  # 0
        review_count=200,  # 0
    )
    assert compute_relevance_score(game) == 6


# --- Plafonnement ---


def test_score_capped_at_100():
    game = _make_game(
        followers=10_000,  # 30
        genres=["Indie", "Adventure", "Casual", "RPG", "Simulation"],  # 25
        tags=["Cute", "Relaxing", "Cozy", "Singleplayer", "2D"],  # 20
        discord_url="https://discord.gg/x",  # 8
        twitter_url="https://twitter.com/x",  # 7  → pré-lancement = 100
        release_date="21 Sep, 2023",  # +10
        review_score="Overwhelmingly Positive",  # +20
        owners_estimate="2,000,000 .. 5,000,000",  # +15
        peak_ccu=80000,  # +10
        review_count=50000,  # +10
    )
    assert compute_relevance_score(game) == 100
